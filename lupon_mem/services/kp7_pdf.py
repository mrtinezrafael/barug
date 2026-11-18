"""Fill KP-7 names without clipping and attach complete names/addresses if needed."""
from __future__ import annotations

from html import escape

import fitz


def _width(text, font, size):
    try:
        return fitz.get_text_length(text, fontname=font, fontsize=size)
    except (ValueError, RuntimeError):
        return fitz.get_text_length(text, fontname="helv", fontsize=size)


def _wrapped(text, width, size=10, font="helv"):
    """Wrap words, including an individual token wider than the available line."""
    line = ""
    for word in str(text or "").split():
        candidate = (line + " " + word).strip()
        if _width(candidate, font, size) <= width:
            line = candidate
            continue
        if line:
            yield line
            line = ""
        for char in word:
            if line and _width(line + char, font, size) > width:
                yield line
                line = char
            else:
                line += char
    if line:
        yield line


def _append_people(doc, people, case_number, role):
    width, height = doc[0].rect.width, doc[0].rect.height
    margin, bottom, y = 48, height - 55, 0
    added = []
    page = None

    def new_page():
        nonlocal page, y
        page = doc.new_page(width=width, height=height)
        added.append(page.number)
        page.insert_text((margin, 48), f"KP FORM NO. 7 - {role.upper()} LIST", fontname="hebo", fontsize=12)
        page.insert_text((margin, 68), "Barangay Case No.: " + str(case_number or ""), fontname="helv", fontsize=10)
        page.draw_line((margin, 80), (width - margin, 80), color=(0.6, 0.6, 0.6), width=0.5)
        y = 103

    def line(text, bold=False):
        nonlocal y
        if page is None or y > bottom:
            new_page()
        if any(ord(char) > 255 for char in text):
            # The PDF base fonts substitute dots for these characters.
            # MuPDF's HTML writer embeds fallback fonts for the actual text.
            tag = "b" if bold else "span"
            page.insert_htmlbox(fitz.Rect(margin, y - 11, width - margin, y + 4),
                                f"<{tag}>{escape(text)}</{tag}>",
                                css="*{margin:0;padding:0} body{font:10pt sans-serif;line-height:1.1}")
        else:
            page.insert_text((margin, y), text, fontname="hebo" if bold else "helv", fontsize=10)
        y += 15

    new_page()
    line(f"The following {role}s are listed on this complaint.")
    y += 10
    for number, person in enumerate(people, 1):
        name_lines = list(_wrapped(f"{number}. {person['name']}", width - margin * 2, font="hebo"))
        if y + 15 * len(name_lines) > bottom:
            new_page()
        for value in name_lines:
            line(value, bold=True)
        for value in _wrapped("Address: " + (person.get("address") or "Address not provided"), width - margin * 2):
            line(value)
        y += 12
    for index, number in enumerate(added, 1):
        footer = f"{role.capitalize()} list - page {index} of {len(added)}"
        doc[number].insert_text((margin, height - 30), footer, fontname="helv", fontsize=8)


def fill_kp7_pdf(doc, name_to_value, people, respondents=()):
    """Map each side to its own name and address widgets and continuation pages."""
    values = dict(name_to_value)
    fields = {w.field_name for page in doc for w in (page.widgets() or [])}
    modern = "Text1" in fields
    respondent_address_widget = modern and "respondents_extra" in fields
    groups = (
        ("complainant", people, values.get("Text1") or values.get("complainants_2") or "",
         values.get("complainants") or "",
         {"Text1", "complainants_2"} if modern else {"complainants", "complainants_2"},
         {"complainants", "complainant"} if modern else set()),
        ("respondent", respondents, values.get("respondents_extra") or "",
         values.get("respondents") or "",
         {"respondents_extra"} if respondent_address_widget else {"respondents", "respondents_extra"},
         {"respondents"} if respondent_address_widget else set()),
    )
    attachments = {}
    field_roles = {}
    address_widgets = set()
    for role, entries, names, address, name_fields, address_fields in groups:
        # Use stored text verbatim for older forms without an identity snapshot.
        addresses = {person["address"].strip() for person in entries}
        if entries:
            names = ", ".join(person["name"] for person in entries)
            if len(addresses) == 1 and entries[0]["address"]:
                address = entries[0]["address"]
        available_names = fields & name_fields
        available_addresses = fields & address_fields
        for field in available_names:
            values[field] = names
        for field in available_addresses:
            values[field] = address
        field_roles.update(dict.fromkeys(available_names | available_addresses, role))
        address_widgets.update(available_addresses)
        if len(addresses) > 1 or (address and not available_addresses) or (names and not available_names):
            attachments[role] = True

    overflow, sizes = set(), {}
    for page in doc:
        for widget in page.widgets() or []:
            field = widget.field_name
            if field not in field_roles:
                continue
            text = str(values.get(field) or "")
            size = max(8.0, min(12.0, float(widget.text_fontsize or 10)))
            width = widget.rect.width - 6
            while size > 8 and _width(text, widget.text_font or "helv", size) > width:
                size = max(8.0, size - 0.5)
            if (any(ord(char) > 255 for char in text)
                    or _width(text, widget.text_font or "helv", size) > width
                    or (widget.text_maxlen and len(text) > widget.text_maxlen)):
                overflow.add(field)
                attachments[field_roles[field]] = True
            sizes[widget.xref] = size

    for page in doc:
        for widget in page.widgets() or []:
            field = widget.field_name
            if field not in values:
                continue
            role = field_roles.get(field)
            text = str(values[field] or "")
            if field in overflow or (role in attachments and field in address_widgets):
                text = f"See attached {role} list"
            if widget.xref in sizes:
                widget.text_fontsize = sizes[widget.xref]
            widget.field_value = text
            widget.update()

    for role, entries, names, address, _, _ in groups:
        if role in attachments:
            listing = entries or [{"name": names, "address": address}]
            _append_people(doc, listing, values.get("case_num"), role)
