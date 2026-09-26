# -*- coding: UTF-8 -*-
"""
Unattended mode: answer Revit warnings, errors and dialogs so scripted runs don't stall.

A modal warning, error or dialog stops a scripted run: the MCP call times out and nothing moves
until someone clicks. Unattended mode subscribes to two Revit events and answers them itself:

- Application.FailuresProcessing: warnings are dismissed; errors get Revit's default resolution
  ("Unjoin Elements", "Remove Constraints", ...); an error with no resolution rolls the transaction
  back (on_error="rollback", the default) or deletes the failing elements (on_error="delete").
  Groups are never broken: an error whose fix would ungroup ("Ungroup", "Fix Groups...") or
  delete group members rolls the transaction back instead (REP 4.5: never ungroup).
  Only transactions whose name starts with one of `prefixes` ("MCP:" by default), or that run
  inside an MCP call, are touched: the user's own edits keep Revit's normal dialogs.
- UIApplication.DialogBoxShowing: task dialogs, message boxes and classic dialogs are answered
  (per-dialog `rules`, else a default answer). By default only while an MCP call is running
  (dialog_scope="mcp"), because the model is shared with the user in real time.

Everything answered is logged in memory (and appended to `log_path` as JSON lines when given).
The mode switches itself off after `minutes`, so it can't be left on by accident.

The execute_code route marks each call as an MCP call. Without that (before pyRevit is reloaded),
wrap the work yourself:

    import revit_mcp.unattended as U
    U.enable(minutes=120)
    with U.mcp_call():
        execfile(r"...\\build_revit.py")
    print(U.summary())
"""
import contextlib
import io
import json
import logging

import System
from System.Collections.Generic import List
from pyrevit import DB, revit
from Autodesk.Revit.DB.Events import FailuresProcessingEventArgs
from Autodesk.Revit.UI.Events import (
    DialogBoxShowingEventArgs,
    MessageBoxShowingEventArgs,
    TaskDialogShowingEventArgs,
)

logger = logging.getLogger(__name__)

# The state lives in the AppDomain, not in this module, so a re-imported or reloaded module still
# finds (and can unsubscribe) the handlers an earlier copy registered.
_KEY = "revit_mcp.unattended"
_LOG_MAX = 1000

# OverrideResult values. Task dialogs take TaskDialogResult (Ok=1, Cancel=2, Yes=6, No=7, Close=8,
# CommandLink1..4=1001..1004); message boxes and classic dialogs take Win32 IDOK=1, IDCANCEL=2,
# IDYES=6, IDNO=7.
TASK_DIALOG_CLOSE = 8
IDOK = 1
IDYES = 6

# Dialogs whose right answer is known. Everything else gets the default for its kind; the log
# shows each DialogId so new ones can be added here or passed in as `rules`.
KNOWN_DIALOGS = {
    # Opening a file with missing links: "Ignore and continue opening the project".
    "TaskDialog_Unresolved_References": 1002,
}

# Revit's default fix for several group errors is "Ungroup" (DetachElements) or "Fix Groups..."
# (FixElements, which opens a dialog); for others it deletes group members. None of these may be
# applied unattended, so every group error, and any error whose fix would ungroup, rolls its
# transaction back. (A message reports its resolution type as "Default"; the real one comes from
# its failure definition, and the caption is checked too.)
_NEVER_RESOLVE = (DB.FailureResolutionType.DetachElements, DB.FailureResolutionType.FixElements)


def _group_failure_guids():
    gf = DB.BuiltInFailures.GroupFailures
    return set(str(getattr(gf, n).Guid) for n in dir(gf)
               if isinstance(getattr(gf, n, None), DB.FailureDefinitionId))


_GROUP_FAILURES = _group_failure_guids()

# Win32 MessageBox button sets (low bits of MessageBoxShowingEventArgs.DialogType).
_MB_YES_ANSWER = {3: IDYES, 4: IDYES}  # MB_YESNOCANCEL, MB_YESNO; the rest take IDOK


def _state():
    return System.AppDomain.CurrentDomain.GetData(_KEY)


def _now():
    return System.DateTime.Now


def _active(st):
    return st is not None and st["on"] and _now() < st["until"]


def _idv(element_id):
    v = getattr(element_id, "Value", None)
    return int(v) if v is not None else element_id.IntegerValue


def _text(value):
    try:
        return unicode(value) if value is not None else u""
    except Exception:
        return u"?"


def _record(st, entry):
    entry["time"] = _now().ToString("HH:mm:ss")
    log = st["log"]
    log.append(entry)
    if len(log) > _LOG_MAX:
        del log[: len(log) - _LOG_MAX]
    key = u"{} | {} | {}".format(entry.get("kind"), entry.get("action"), entry.get("text", u"")[:120])
    st["counts"][key] = st["counts"].get(key, 0) + 1
    if st.get("log_path"):
        try:
            with io.open(st["log_path"], "a", encoding="utf-8") as f:
                f.write(unicode(json.dumps(entry)) + u"\n")
        except Exception:
            pass


def _would_break_group(fa, f):
    fid = f.GetFailureDefinitionId()
    if str(fid.Guid) in _GROUP_FAILURES:
        return True
    if not f.HasResolutions():
        return False
    rtype = f.GetCurrentResolutionType()
    if rtype == DB.FailureResolutionType.Default:
        d = fa.GetDocument().Application.GetFailureDefinitionRegistry().FindFailureDefinition(fid)
        if d is not None and d.HasResolutions():
            rtype = d.GetDefaultResolutionType()
    caption = _text(f.GetDefaultResolutionCaption()).lower()
    return rtype in _NEVER_RESOLVE or "ungroup" in caption or "fix group" in caption


def _matches(name, prefixes):
    return any(name.startswith(p) for p in prefixes)


def _on_failures(sender, args):
    st = _state()
    if not _active(st):
        return
    try:
        fa = args.GetFailuresAccessor()
        tname = _text(fa.GetTransactionName())
        if not (st["busy"] > 0 or _matches(tname, st["prefixes"])):
            return
        resolved = False
        rollback = False
        for f in list(fa.GetFailureMessages()):
            # Read everything first: the accessor is invalid once the failure is dismissed.
            sev = f.GetSeverity()
            ids = [_idv(i) for i in f.GetFailingElementIds()]
            text = _text(f.GetDescriptionText())
            if sev == DB.FailureSeverity.Warning:
                fa.DeleteWarning(f)
                action = "dismissed warning"
            elif sev == DB.FailureSeverity.Error:
                if _would_break_group(fa, f):
                    action = "rolled back transaction (group error: never ungroup)"
                    rollback = True
                elif f.HasResolutions():
                    action = u"resolved: " + _text(f.GetDefaultResolutionCaption())
                    fa.ResolveFailure(f)
                    resolved = True
                elif st["on_error"] == "delete" and ids:
                    fa.DeleteElements(List[DB.ElementId](f.GetFailingElementIds()))
                    action = "deleted failing elements"
                    resolved = True
                else:
                    action = "rolled back transaction"
                    rollback = True
            else:
                action = u"left for Revit ({})".format(sev)
            entry = {"kind": "failure", "transaction": tname, "severity": str(sev),
                     "text": text, "ids": ids[:20], "action": action}
            _record(st, entry)
        if rollback:
            opts = fa.GetFailureHandlingOptions()
            opts.SetClearAfterRollback(True)
            fa.SetFailureHandlingOptions(opts)
            args.SetProcessingResult(DB.FailureProcessingResult.ProceedWithRollBack)
        elif resolved:
            args.SetProcessingResult(DB.FailureProcessingResult.ProceedWithCommit)
    except Exception as ex:
        _record(st, {"kind": "handler-error", "action": "failures", "text": _text(ex)})


def _on_dialog(sender, args):
    st = _state()
    if not _active(st) or not st["dialogs"]:
        return
    if st["dialog_scope"] == "mcp" and st["busy"] <= 0:
        return
    try:
        dialog_id = _text(args.DialogId)
        if isinstance(args, TaskDialogShowingEventArgs):
            kind, message, answer = "task dialog", _text(args.Message), TASK_DIALOG_CLOSE
        elif isinstance(args, MessageBoxShowingEventArgs):
            kind, message = "message box", _text(args.Message)
            answer = _MB_YES_ANSWER.get(args.DialogType & 0xF, IDOK)
        else:
            kind, message, answer = "dialog", u"", IDOK
        answer = st["rules"].get(dialog_id, KNOWN_DIALOGS.get(dialog_id, answer))
        if answer is None:
            action = "left open (rule)"
        else:
            ok = args.OverrideResult(int(answer))
            action = u"answered {}{}".format(answer, "" if ok else " (refused)")
        _record(st, {"kind": kind, "dialog_id": dialog_id, "text": message, "action": action})
    except Exception as ex:
        _record(st, {"kind": "handler-error", "action": "dialog", "text": _text(ex)})


def _unsubscribe():
    st = _state()
    if st and st.get("delegates"):
        uiapp, fdel, ddel = st["delegates"]
        try:
            uiapp.Application.FailuresProcessing -= fdel
        except Exception:
            pass
        try:
            uiapp.DialogBoxShowing -= ddel
        except Exception:
            pass
        st["delegates"] = None


def enable(minutes=120, prefixes=("MCP:",), on_error="rollback", dialogs=True,
           dialog_scope="mcp", rules=None, log_path=None, uiapp=None):
    """Switch unattended mode on (or re-configure it) for `minutes`.

    prefixes: transaction-name prefixes whose failures are handled; empty = every transaction.
    on_error: "rollback" (default) or "delete" for errors Revit offers no resolution for.
    dialog_scope: "mcp" (only during MCP calls) or "all" (any dialog while the mode is on).
    rules: {DialogId: answer or None to leave it open}, on top of KNOWN_DIALOGS.
    """
    if on_error not in ("rollback", "delete"):
        raise ValueError("on_error must be 'rollback' or 'delete'")
    if dialog_scope not in ("mcp", "all"):
        raise ValueError("dialog_scope must be 'mcp' or 'all'")
    uiapp = uiapp or revit.HOST_APP.uiapp
    old = _state()
    _unsubscribe()
    st = {
        "on": True,
        "since": _now(),
        "until": _now().AddMinutes(float(minutes)),
        "prefixes": tuple(prefixes or ()) or ("",),
        "on_error": on_error,
        "dialogs": bool(dialogs),
        "dialog_scope": dialog_scope,
        "rules": dict(rules or {}),
        "log_path": log_path,
        "busy": old["busy"] if old else 0,
        "log": old["log"] if old else [],
        "counts": old["counts"] if old else {},
        "delegates": None,
    }
    fdel = System.EventHandler[FailuresProcessingEventArgs](_on_failures)
    ddel = System.EventHandler[DialogBoxShowingEventArgs](_on_dialog)
    uiapp.Application.FailuresProcessing += fdel
    uiapp.DialogBoxShowing += ddel
    st["delegates"] = (uiapp, fdel, ddel)
    System.AppDomain.CurrentDomain.SetData(_KEY, st)
    logger.info("Unattended mode on for %s min", minutes)
    return status()


def disable():
    """Switch unattended mode off and unsubscribe the handlers. The log is kept."""
    st = _state()
    _unsubscribe()
    if st:
        st["on"] = False
    return status()


def status():
    st = _state()
    if st is None:
        return {"enabled": False, "handled": 0}
    return {
        "enabled": _active(st),
        "expired": st["on"] and not _active(st),
        "until": st["until"].ToString("yyyy-MM-dd HH:mm:ss"),
        "transaction_prefixes": [p for p in st["prefixes"] if p],
        "on_error": st["on_error"],
        "dialogs": st["dialogs"],
        "dialog_scope": st["dialog_scope"],
        "mcp_call_running": st["busy"] > 0,
        "handled": len(st["log"]),
        "log_path": st["log_path"],
    }


def recent(limit=50):
    """The last `limit` handled warnings/errors/dialogs, newest last."""
    st = _state()
    return list(st["log"][-int(limit):]) if st else []


def summary(limit=20):
    """Distinct handled messages with how often each came up, most frequent first."""
    st = _state()
    if not st:
        return []
    rows = sorted(st["counts"].items(), key=lambda kv: -kv[1])[: int(limit)]
    return [{"count": n, "message": k} for k, n in rows]


def clear_log():
    st = _state()
    if st:
        del st["log"][:]
        st["counts"].clear()
    return status()


@contextlib.contextmanager
def mcp_call():
    """Mark the enclosed code as an MCP call: its failures and dialogs are handled whatever the
    transaction names (when the mode is on)."""
    st = _state()
    if st is not None:
        st["busy"] += 1
    try:
        yield
    finally:
        if st is not None:
            st["busy"] -= 1


def register_unattended_routes(api):
    """POST /unattended/ {"action": "enable" | "disable" | "status" | "log" | "clear", ...}."""
    from pyrevit import routes

    @api.route("/unattended/", methods=["POST"])
    def unattended(doc, request):
        try:
            data = json.loads(request.data) if isinstance(request.data, str) else (request.data or {})
            action = data.get("action", "status")
            if action == "enable":
                result = enable(
                    minutes=data.get("minutes", 120),
                    prefixes=data.get("transaction_prefixes") or ("MCP:",),
                    on_error=data.get("on_error", "rollback"),
                    dialogs=data.get("dialogs", True),
                    dialog_scope=data.get("dialog_scope", "mcp"),
                    rules=data.get("rules"),
                    log_path=data.get("log_path"),
                )
            elif action == "disable":
                result = disable()
            elif action == "status":
                result = status()
            elif action == "log":
                result = status()
                result["summary"] = summary(data.get("limit", 20))
                result["recent"] = recent(data.get("limit", 20))
            elif action == "clear":
                result = clear_log()
            else:
                return routes.make_response(
                    data={"error": "Unknown action '{}'".format(action)}, status=400
                )
            result["status"] = "success"
            return routes.make_response(data=result)
        except Exception as ex:
            logger.error("unattended route failed: %s", ex)
            return routes.make_response(data={"error": str(ex)}, status=500)
