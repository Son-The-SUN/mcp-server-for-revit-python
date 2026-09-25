# -*- coding: utf-8 -*-
"""Tests for the HTTP helpers in main.py when Revit is unreachable."""
import httpx
from unittest.mock import AsyncMock, patch

import main


@patch("main.httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=httpx.ConnectError("refused"))
async def test_revit_get_connect_error_is_actionable(mock_get):
    result = await main.revit_get("/status/")

    assert result == main.CONNECT_ERROR
    assert "pyRevit" in result


@patch("main.httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=httpx.ConnectError("refused"))
async def test_revit_post_connect_error_is_actionable(mock_post):
    result = await main.revit_post("/execute_code/", {"code": "print(1)"})

    assert result == main.CONNECT_ERROR


@patch("main.httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=httpx.ConnectError("refused"))
async def test_revit_image_connect_error_is_actionable(mock_get):
    result = await main.revit_image("/get_view/Level 1")

    assert result == main.CONNECT_ERROR


def test_connect_error_not_mistaken_for_server_error():
    # _wait_for_revit_ready treats "Error: 5..." as "Routes is up but returned 5xx"
    assert not main.CONNECT_ERROR.startswith("Error: 5")
