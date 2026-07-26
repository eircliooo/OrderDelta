"""FastAPI 应用入口。

**默认绑定 127.0.0.1**（app/core/config.py）。MVP 无身份认证——回环绑定就是
替代鉴权的手段。改成 0.0.0.0 会把客户订单数据暴露到局域网。

前端构建产物由本进程静态托管：单容器、单端口、无 CORS。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.core.config import settings
from app.db.session import init_db
from app.services.projects import ServiceError

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def create_app() -> FastAPI:
    app = FastAPI(
        title="外贸订单差异雷达",
        version="0.1.0",
        description=(
            "辅助核对报价单 / 客户 PO / 形式发票的差异。"
            "本工具只能辅助核对，不判断哪份文件正确，不构成贸易、法律或财务结论。"
        ),
    )
    init_db()
    app.include_router(router)

    @app.exception_handler(ServiceError)
    async def _service_error(_: Request, exc: ServiceError) -> JSONResponse:
        """错误响应不泄露服务器绝对路径（SPEC §15.1）。"""
        return JSONResponse(
            status_code=exc.status_code,
            content={"error_code": exc.code, "message": exc.message},
        )

    # 前端构建产物存在时才挂载，便于纯后端开发
    if FRONTEND_DIST.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=FRONTEND_DIST / "assets"),
            name="assets",
        )

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str) -> FileResponse:
            index = FRONTEND_DIST / "index.html"
            return FileResponse(index)

    return app


app = create_app()


def main() -> None:  # pragma: no cover - 手动启动入口
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":  # pragma: no cover
    main()
