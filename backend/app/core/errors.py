"""统一异常处理，保证响应都是 {code, msg, data} 格式。"""
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_exc(_: Request, exc: StarletteHTTPException):
        # 我们的 HTTPException.detail 形如 "需要平台管理员权限"
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.status_code, "msg": str(exc.detail), "data": None},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exc(_: Request, exc: RequestValidationError):
        # jsonable_encoder：自定义 validator 抛 ValueError 时，errors() 的 ctx 会带
        # 不可 JSON 序列化的异常对象，直接塞进 JSONResponse 会 500——先编码成可序列化形式。
        return JSONResponse(
            status_code=422,
            content={"code": 422, "msg": "参数校验失败", "data": jsonable_encoder(exc.errors())},
        )

    @app.exception_handler(Exception)
    async def global_exc(_: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content={"code": 500, "msg": f"服务器内部错误: {exc}", "data": None},
        )
