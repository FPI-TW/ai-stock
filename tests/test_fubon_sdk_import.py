"""富邦 SDK 安裝驗證。

只確認「這個平台裝得起來、import 得到」。不登入、不連線、不需要憑證，
所以 CI（無券商憑證）也必須綠。跨平台 wheel 若裝錯版本，會在這裡就爆，
而不是等到部署後才發現。
"""


def test_fubon_sdk_importable() -> None:
    from fubon_neo.sdk import FubonSDK

    assert callable(FubonSDK)
