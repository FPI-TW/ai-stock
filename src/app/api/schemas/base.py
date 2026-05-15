from pydantic import BaseModel, ConfigDict


class OwnerScopedRequestModel(BaseModel):
    """所有 owner-scoped mutation request payload 的基底。

    Owner 一律由 app.core.security.get_current_user 從 context 注入；
    request body 禁止含 owner_user_id 或任何未宣告欄位（extra="forbid"）。
    """

    model_config = ConfigDict(extra="forbid")
