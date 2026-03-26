from app.core.response import ApiError, ApiErrorDetail, ApiSuccess, MetaPayload

Meta = MetaPayload
SuccessEnvelope = ApiSuccess

__all__ = [
    "ApiError",
    "ApiErrorDetail",
    "ApiSuccess",
    "Meta",
    "MetaPayload",
    "SuccessEnvelope",
]
