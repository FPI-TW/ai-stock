"""富邦 SDK 安裝驗證。

只確認「這個平台裝得起來、import 得到」。不登入、不連線、不需要憑證，
所以 CI（無券商憑證）也必須綠。wheel 若裝錯版本，會在這裡就爆，
而不是等到部署後才發現。

只有 Linux wheel 進版控（見 pyproject.toml 的 [tool.uv.sources]），macOS 開發機的
venv 不會安裝 fubon_neo，故本測試在非 Linux 平台跳過；CI 與正式環境都是 Linux，
覆蓋率不受影響。
"""

import sys

import pytest


@pytest.mark.skipif(sys.platform != "linux", reason="只有 Linux wheel 進版控，其他平台不安裝 fubon_neo")
def test_fubon_sdk_importable() -> None:
    from fubon_neo.sdk import FubonSDK

    assert callable(FubonSDK)
