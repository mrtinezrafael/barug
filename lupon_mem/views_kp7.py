"""KP-7 routes; all other forms continue through the original API views."""
from __future__ import annotations

import json
import logging

from django.db import DatabaseError, connection, transaction
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_protect

from authentication.guards import role_required, session_login_required
from .services import kp7_parties as parties

logger = logging.getLogger(__name__)
LUPON_ROLES = {"BARANGAY LUPON MEMBER", "BARANGAY LUPON PRESIDENT"}


def _error(exc):
    status = 409 if isinstance(exc, parties.PartyConflictError) else 400
    return JsonResponse({"ok": False, "error": str(exc)}, status=status)


@session_login_required
@role_required(*LUPON_ROLES)
@require_GET
@never_cache
def form_builder(request, case_id, form_id=None, submission_id=None):
    """Honor both the existing ?submission_id= link and the path-based editor."""
    try:
        raw_sid = submission_id or request.GET.get("submission_id")
        sid = parties.positive_int(raw_sid, "Submission ID") if raw_sid else None
        if sid:
            with connection.cursor() as cur:
                cur.execute("SELECT blotter_case_id, form_id FROM blotter_kp_submission WHERE submission_id=%s", [sid])
                row = cur.fetchone()
                if not row or int(row[0]) != int(case_id) or (form_id is not None and int(row[1]) != int(form_id)):
                    raise parties.PartyInputError("The submission does not belong to this case and form.")
                if int(row[1]) == 7:
                    parties.validate_submission(cur, sid, int(case_id))
        elif int(form_id or 0) == 7:
            with connection.cursor() as cur:
                parties.fetch_case(cur, int(case_id))
    except parties.PartyInputError as exc:
        return _error(exc)
    from .views import form_builder as original_builder
    return original_builder(request, case_id=case_id, form_id=form_id, submission_id=sid)


@session_login_required
@role_required(*LUPON_ROLES)
@require_GET
@never_cache
def case_parties(request, case_id, role=parties.COMPLAINANT):
    try:
        sid = request.GET.get("submission_id")
        sid = parties.positive_int(sid, "Submission ID") if sid else None
        with connection.cursor() as cur:
            state = parties.load_state(cur, int(case_id), sid, role)
        return JsonResponse({"ok": True, **state})
    except parties.PartyInputError as exc:
        return _error(exc)
    except DatabaseError:
        logger.exception("KP-7 %s lookup failed for case %s", role.singular, case_id)
        return JsonResponse({"ok": False, "error": f"Could not load {role.singular}s. Please try again."}, status=500)


@session_login_required
@role_required(*LUPON_ROLES)
@require_GET
@never_cache
def people_search(request):
    try:
        with connection.cursor() as cur:
            results = parties.search_people(cur, request.GET.get("q", ""))
        return JsonResponse({"ok": True, "results": results})
    except DatabaseError:
        logger.exception("KP-7 person search failed")
        return JsonResponse({"ok": False, "error": "Could not search people. Please try again."}, status=500)


@session_login_required
@role_required(*LUPON_ROLES)
@require_POST
@csrf_protect
def save_answers(request):
    try:
        form_id = parties.positive_int(request.POST.get("form_id"), "Form ID")
    except parties.PartyInputError as exc:
        return _error(exc)
    if form_id != 7:
        from .views_api import save_answers as original_save
        return original_save(request)

    try:
        case_id = parties.positive_int(request.POST.get("case_id"), "Case ID")
        sid = request.POST.get("submission_id")
        sid = parties.positive_int(sid, "Submission ID") if sid else None
        if "kp7_complainants" not in request.POST:
            raise parties.PartyInputError("Reload Form 7 to select its complainants, then save again.")
        answers = json.loads(request.POST.get("answers", "{}"))
        staff_id = (request.session.get("auth") or {}).get("staff_id") or None
        with transaction.atomic():
            with connection.cursor() as cur:
                result = parties.save_form7(
                    cur, case_id=case_id, submission_id=sid, answers=answers,
                    selection=request.POST["kp7_complainants"],
                    respondent_selection=request.POST.get("kp7_respondents"), staff_id=staff_id,
                )
        return JsonResponse({"ok": True, **result})
    except (ValueError, TypeError) as exc:
        if isinstance(exc, parties.PartyInputError):
            return _error(exc)
        return JsonResponse({"ok": False, "error": "Invalid form data. Reload Form 7 and try again."}, status=400)
    except DatabaseError:
        logger.exception("KP-7 save failed")
        return JsonResponse({"ok": False, "error": "Could not save Form 7. No partial changes were saved."}, status=500)


@session_login_required
@role_required(*LUPON_ROLES)
@require_POST
@csrf_protect
def submit_form(request):
    try:
        data = json.loads(request.body.decode("utf-8"))
        if not isinstance(data, dict):
            raise parties.PartyInputError("Invalid submission data.")
        sid = parties.positive_int(data.get("submission_id"), "Submission ID")
        case_id = parties.positive_int(data.get("case_id"), "Case ID")
        with connection.cursor() as cur:
            cur.execute("SELECT form_id FROM blotter_kp_submission WHERE submission_id=%s", [sid])
            row = cur.fetchone()
            if not row:
                raise parties.PartyInputError("Submission not found.")
            if int(row[0]) == 7:
                parties.validate_submission(cur, sid, case_id)
                if data.get("answers"):
                    raise parties.PartyInputError("Save Form 7 before generating the PDF.")
                if not parties.load_snapshot(cur, sid):
                    raise parties.PartyInputError("Reload and save Form 7 before generating the PDF.")
    except (ValueError, TypeError, UnicodeError) as exc:
        if isinstance(exc, parties.PartyInputError):
            return _error(exc)
        return JsonResponse({"ok": False, "error": "Invalid submission data."}, status=400)
    except DatabaseError:
        logger.exception("KP-7 submission validation failed")
        return JsonResponse({"ok": False, "error": "Could not validate the submission. Please try again."}, status=500)

    # Keep the project's existing upload, logging, notification and status flow.
    from .views_api import submit_form as original_submit
    return original_submit(request)
