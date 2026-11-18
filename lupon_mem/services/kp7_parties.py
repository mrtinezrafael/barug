"""KP-7 party identities, case links, and per-submission snapshots.

All database functions take a cursor. The caller owns the transaction.
Names always come from person; address overrides belong only to the form.
"""
from __future__ import annotations

import json
from typing import NamedTuple


class PartyRole(NamedTuple):
    # Values are constants defined here. Table names never come from HTTP input.
    singular: str
    name_codes: tuple[str, ...]
    address_code: str

    @property
    def snapshot_code(self):
        return f"kp7_{self.singular}_parties"


COMPLAINANT = PartyRole("complainant", ("complainants", "complainants_2", "complainant_printed_name"), "contact_one")
RESPONDENT = PartyRole("respondent", ("respondents", "respondents_2"), "recipient_address")
MAX_PEOPLE = 100
MAX_ADDRESS_LENGTH = 2000


class PartyInputError(ValueError):
    pass


class PartyConflictError(PartyInputError):
    pass


def positive_int(value, label="ID"):
    if isinstance(value, bool) or not str(value).isdigit() or int(value) < 1:
        raise PartyInputError(f"{label} must be a positive integer.")
    return int(value)


def clean_text(value):
    return " ".join(str(value or "").split())


def decode_json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def parse_selection(value, role=COMPLAINANT):
    value = decode_json(value)
    if not isinstance(value, list):
        raise PartyInputError(f"Invalid {role.singular} selection.")
    if not value and role == COMPLAINANT:
        raise PartyInputError("Select at least one complainant.")
    if len(value) > MAX_PEOPLE:
        raise PartyInputError(f"A form can contain at most {MAX_PEOPLE} {role.singular}s.")
    selected, seen = [], set()
    for item in value:
        if not isinstance(item, dict):
            raise PartyInputError(f"Invalid {role.singular} selection.")
        pid = positive_int(item.get("person_id"), "Person ID")
        if pid in seen:
            raise PartyInputError(f"Select each {role.singular} only once.")
        address = item.get("address", "")
        if not isinstance(address, str) or len(address) > MAX_ADDRESS_LENGTH:
            raise PartyInputError(f"Each address must be text of at most {MAX_ADDRESS_LENGTH} characters.")
        selected.append({"person_id": pid, "address": clean_text(address)})
        seen.add(pid)
    return selected


def names_text(people):
    return ", ".join(p["name"] for p in people)


def addresses_text(people):
    addresses = [clean_text(p.get("address")) for p in people]
    if addresses and addresses[0] and all(a == addresses[0] for a in addresses):
        return addresses[0]
    return "; ".join(f"{p['name']}: {a or 'Address not provided'}" for p, a in zip(people, addresses))


_PERSON_COLUMNS = """
    p.person_id, p.first_name, p.middle_name, p.last_name, p.suffix,
    TRIM(CONCAT_WS(', ', NULLIF(ps.purok_sitio_name, ''), NULLIF(a.street, ''),
        CASE WHEN COALESCE(a.barangay, '') <> '' THEN 'Brgy. ' || a.barangay END,
        NULLIF(a.city, ''))) AS full_address
"""
_PERSON_JOINS = """
    FROM person p
    LEFT JOIN addresss a ON a.address_id = p.address_id
    LEFT JOIN purok_sitio ps ON ps.purok_sitio_id = a.purok_sitio_id
"""


def _person(row):
    pid, first, middle, last, suffix, address = row
    return {"person_id": int(pid), "name": clean_text(" ".join(x or "" for x in (first, middle, last, suffix))),
            "address": clean_text(address)}


def fetch_people(cur, person_ids):
    if not person_ids:
        return []
    placeholders = ",".join(["%s"] * len(person_ids))
    cur.execute(f"SELECT {_PERSON_COLUMNS} {_PERSON_JOINS} WHERE p.person_id IN ({placeholders})", person_ids)
    found = {int(row[0]): _person(row) for row in cur.fetchall()}
    if any(pid not in found or not found[pid]["name"] for pid in person_ids):
        raise PartyInputError("A selected person no longer exists or has no name. Reload the form.")
    return [found[pid] for pid in person_ids]


def search_people(cur, query):
    query = clean_text(query)[:100]
    if len(query) < 2:
        return []
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    cur.execute(f"""
        SELECT {_PERSON_COLUMNS} {_PERSON_JOINS}
        WHERE LOWER(CONCAT_WS(' ', p.first_name, p.middle_name, p.last_name, p.suffix))
              LIKE LOWER(%s) ESCAPE '\\'
        ORDER BY p.last_name, p.first_name, p.person_id LIMIT 20
    """, [f"%{escaped}%"])
    return [_person(row) for row in cur.fetchall()]


def fetch_case(cur, case_id, lock=False):
    cur.execute("SELECT blotter_report_id FROM blotter_case WHERE blotter_case_id=%s" +
                (" FOR UPDATE" if lock else ""), [case_id])
    row = cur.fetchone()
    if row is None:
        raise PartyInputError("Case not found.")
    return row[0]


def case_person_ids(cur, case_id, role=COMPLAINANT):
    cur.execute(f"SELECT person_id FROM {role.singular}_case WHERE blotter_case_id=%s ORDER BY person_id", [case_id])
    return [int(row[0]) for row in cur.fetchall()]


def validate_submission(cur, submission_id, case_id, lock=False):
    cur.execute("""
        SELECT s.blotter_case_id, s.form_id, s.status, k.form_code
        FROM blotter_kp_submission s JOIN kp_form k ON k.form_id=s.form_id
        WHERE s.submission_id=%s
    """ + (" FOR UPDATE OF s" if lock else ""), [submission_id])
    row = cur.fetchone()
    if not row or int(row[0]) != case_id or int(row[1]) != 7 or str(row[3]).upper() != "KP-7":
        raise PartyInputError("The submission does not belong to this case's Form 7.")
    if str(row[2]).upper() == "VOID":
        raise PartyInputError("A void submission cannot be edited.")


def load_snapshot(cur, submission_id, role=COMPLAINANT):
    if not submission_id:
        return []
    cur.execute("""
        SELECT a.value_json FROM blotter_kp_answer a
        JOIN kp_field f ON f.field_id=a.field_id
        WHERE a.submission_id=%s AND f.field_code=%s
    """, [submission_id, role.snapshot_code])
    row = cur.fetchone()
    return snapshot_people(row[0]) if row else []


def snapshot_people(value):
    data = decode_json(value)
    if not isinstance(data, dict) or data.get("version") != 1:
        return []
    people = data.get("people")
    if not isinstance(people, list):
        return []
    if any(not isinstance(p, dict) or not isinstance(p.get("person_id"), int)
           or isinstance(p.get("person_id"), bool) or p["person_id"] < 1
           or not isinstance(p.get("name"), str) or not isinstance(p.get("address"), str) for p in people):
        return []
    return people


def load_state(cur, case_id, submission_id=None, role=COMPLAINANT):
    fetch_case(cur, case_id)
    if submission_id:
        validate_submission(cur, submission_id, case_id)
    linked_ids = case_person_ids(cur, case_id, role)
    snapshot = load_snapshot(cur, submission_id, role)
    snapshot_by_id = {p["person_id"]: p for p in snapshot}
    ordered_ids = list(dict.fromkeys([p["person_id"] for p in snapshot] + linked_ids))
    people = fetch_people(cur, ordered_ids)
    for person in people:
        saved = snapshot_by_id.get(person["person_id"])
        if saved:
            person.update(name=saved["name"], address=saved["address"])
        person["linked"] = person["person_id"] in linked_ids
    legacy_names, legacy_address = "", ""
    keep_legacy = False
    if submission_id and not snapshot:
        cur.execute("""
            SELECT f.field_code, a.value_json FROM blotter_kp_answer a
            JOIN kp_field f ON f.field_id=a.field_id
            WHERE a.submission_id=%s AND f.field_code IN (%s, %s)
        """, [submission_id, role.name_codes[0], role.address_code])
        legacy = {code: decode_json(value) for code, value in cur.fetchall()}
        legacy_names = str(legacy.get(role.name_codes[0]) or "")
        legacy_address = str(legacy.get(role.address_code) or "")
        if role == RESPONDENT:
            # Free text has no person ID. A single old address may describe only
            # one of several people. Keep the original until staff review it.
            keep_legacy = bool(
                (legacy_names and clean_text(legacy_names).casefold() != names_text(people).casefold())
                or (legacy_address and len(people) != 1)
            )
        if len(people) == 1 and legacy_address and not keep_legacy:
            people[0]["address"] = legacy_address
    return {"people": people, "legacy_names": legacy_names, "legacy_address": legacy_address,
            "keep_legacy": keep_legacy}


def selected_people(cur, case_id, selection, role):
    selected = parse_selection(selection, role)
    ids = [pick["person_id"] for pick in selected]
    if not set(case_person_ids(cur, case_id, role)).issubset(ids):
        raise PartyConflictError(f"The case's {role.singular} list changed. Reload Form 7 to include everyone already recorded.")
    people = fetch_people(cur, ids)
    for person, pick in zip(people, selected):
        person["address"] = pick["address"]
    return people


def record_people(cur, case_id, report_id, role, people):
    for person in people:
        pid = person["person_id"]
        cur.execute(f"""
            INSERT INTO {role.singular}_case (blotter_case_id, person_id)
            VALUES (%s, %s) ON CONFLICT DO NOTHING
        """, [case_id, pid])
        if report_id is not None:
            cur.execute(f"""
                INSERT INTO {role.singular}_report (person_id, blotter_report_id)
                VALUES (%s, %s) ON CONFLICT DO NOTHING
            """, [pid, report_id])


def canonical_answers(role, people):
    names = names_text(people)
    return {**dict.fromkeys(role.name_codes, names), role.address_code: addresses_text(people),
            role.snapshot_code: {"version": 1, "people": people}}


def save_form7(cur, *, case_id, submission_id, answers, selection, respondent_selection=None, staff_id=None):
    """Save both roles and form answers together under the caller's transaction.

    Older clients omit respondent_selection. The upgraded editor can send JSON
    null to keep ambiguous respondent text on an older submission exactly as filed.
    """
    report_id = fetch_case(cur, case_id, lock=True)
    if submission_id:
        validate_submission(cur, submission_id, case_id, lock=True)
    cur.execute("""
        SELECT f.field_id, f.field_code, kff.is_required FROM kp_form_field kff
        JOIN kp_field f ON f.field_id=kff.field_id WHERE kff.form_id=7
    """)
    catalog = cur.fetchall()
    fields = {code: int(fid) for fid, code, _ in catalog}
    required_by_form = {code for _, code, required in catalog if required}
    required = {COMPLAINANT.snapshot_code, "complainants", "complainants_2", "contact_one"}
    if respondent_selection is not None:
        required.update((RESPONDENT.snapshot_code, "respondents", RESPONDENT.address_code))
    if not required.issubset(fields):
        raise PartyInputError("Form 7 setup is incomplete. Run sql/enable_kp7_multiple_parties.sql first.")
    if not isinstance(answers, dict):
        raise PartyInputError("Invalid form answers.")
    allowed = set(fields.values())
    supplied = {}
    for key, value in answers.items():
        fid = positive_int(key, "Field ID")
        if fid not in allowed:
            raise PartyInputError("An answer does not belong to Form 7. Reload the form.")
        supplied[fid] = value

    # Internal answers can only be written from validated person IDs.
    for role in (COMPLAINANT, RESPONDENT):
        supplied.pop(fields.get(role.snapshot_code), None)

    people = selected_people(cur, case_id, selection, COMPLAINANT)
    respondents = None
    if respondent_selection is not None:
        decoded = decode_json(respondent_selection)
        if decoded is None:
            if not submission_id:
                raise PartyInputError("Saved respondent details require an existing Form 7.")
        else:
            respondents = selected_people(cur, case_id, decoded, RESPONDENT)
            if not respondents and "respondents" in required_by_form:
                raise PartyInputError("Select at least one respondent.")
            if not respondents and submission_id:
                state = load_state(cur, case_id, submission_id, RESPONDENT)
                if state["legacy_names"] or state["legacy_address"]:
                    raise PartyInputError("Select a respondent or keep the saved respondent details.")
    if respondents is None and submission_id:
        # Old clients may still send a single generic respondent field. Once a
        # snapshot exists they must never replace it or its derived answers.
        if respondent_selection is not None or load_snapshot(cur, submission_id, RESPONDENT):
            for code in (*RESPONDENT.name_codes, RESPONDENT.address_code):
                supplied.pop(fields.get(code), None)
    if not submission_id:
        cur.execute("""
            INSERT INTO blotter_kp_submission (blotter_case_id, form_id, status, submitted_by_id)
            VALUES (%s, 7, 'DRAFT', %s) RETURNING submission_id
        """, [case_id, staff_id])
        submission_id = int(cur.fetchone()[0])
    record_people(cur, case_id, report_id, COMPLAINANT, people)
    canonical = canonical_answers(COMPLAINANT, people)
    if respondents is not None:
        record_people(cur, case_id, report_id, RESPONDENT, respondents)
        canonical.update(canonical_answers(RESPONDENT, respondents))
    for code, value in canonical.items():
        if code in fields:
            supplied[fields[code]] = value
    for fid, value in supplied.items():
        cur.execute("""
            INSERT INTO blotter_kp_answer (submission_id, field_id, value_json)
            VALUES (%s, %s, %s) ON CONFLICT (submission_id, field_id)
            DO UPDATE SET value_json=EXCLUDED.value_json
        """, [submission_id, fid, json.dumps(value, ensure_ascii=False)])
    return {"submission_id": submission_id, "people": [dict(p, linked=True) for p in people],
            "names": canonical["complainants"], "address": canonical["contact_one"],
            "respondents": None if respondents is None else [dict(p, linked=True) for p in respondents]}
