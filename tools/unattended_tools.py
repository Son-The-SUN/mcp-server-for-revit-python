# -*- coding: utf-8 -*-
"""Unattended mode tool: let Revit warnings, errors and dialogs be answered automatically."""

from typing import List, Optional

from mcp.server.fastmcp import Context
from .utils import format_response


def register_unattended_tools(mcp, revit_get, revit_post):
    """Register the unattended mode tool with the MCP server."""
    _ = revit_get  # kept for interface consistency

    @mcp.tool()
    async def unattended_mode(
        action: str = "status",
        minutes: int = 120,
        on_error: str = "rollback",
        dialogs: bool = True,
        dialog_scope: str = "mcp",
        transaction_prefixes: Optional[List[str]] = None,
        log_path: Optional[str] = None,
        limit: int = 20,
        ctx: Context = None,
    ) -> str:
        """
        Answer Revit warnings, errors and dialogs automatically during long scripted runs.

        A modal warning or dialog in Revit stalls every call until someone clicks it. With
        unattended mode on, Revit's failures and dialogs are answered and logged instead:
        - warnings are dismissed;
        - errors get Revit's default resolution (e.g. "Unjoin Elements"); an error with no
          resolution rolls its transaction back (on_error="rollback") or deletes the failing
          elements (on_error="delete");
        - task dialogs are closed, message boxes answered OK/Yes.

        Only transactions named with one of transaction_prefixes (default ["MCP:"]) or run
        through execute_revit_code are touched, and dialogs only during MCP calls unless
        dialog_scope="all", so the user's own edits keep Revit's normal dialogs. The mode
        switches itself off after `minutes`.

        Args:
            action: "enable", "disable", "status", "log" (summary + recent entries) or "clear".
            minutes: how long the mode stays on (enable only).
            on_error: "rollback" or "delete" for errors without a resolution.
            dialogs: also answer dialogs (enable only).
            dialog_scope: "mcp" (only during MCP calls) or "all".
            transaction_prefixes: transaction-name prefixes whose failures are handled.
            log_path: optional file to append every handled message to (JSON lines).
            limit: entries returned by "log".
        """
        data = {"action": action}
        if action == "enable":
            data.update(
                {
                    "minutes": minutes,
                    "on_error": on_error,
                    "dialogs": dialogs,
                    "dialog_scope": dialog_scope,
                }
            )
            if transaction_prefixes:
                data["transaction_prefixes"] = transaction_prefixes
            if log_path:
                data["log_path"] = log_path
        elif action == "log":
            data["limit"] = limit
        try:
            response = await revit_post("/unattended/", data, ctx)
            return format_response(response)
        except Exception as e:
            error_msg = "Error in unattended_mode: {}".format(str(e))
            if ctx:
                await ctx.error(error_msg)
            return error_msg
