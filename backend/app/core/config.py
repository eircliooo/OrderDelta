"""运行配置。SPEC §15.2 第 3 条。

**默认绑定 127.0.0.1。** MVP 无身份认证——回环绑定就是替代鉴权的廉价手段。
改成 0.0.0.0 会让同一局域网内任何人无需密码打开你的订单数据。
**禁止**由此引入登录 / Token / HTTPS / 限流。
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    #: `env_file` 用**绝对路径**指向仓库根的 `.env`。
    #:
    #: 写相对路径 `".env"` 的话它是相对**进程 CWD** 的，而文档化的启动命令一律在
    #: `backend/` 下执行 —— 于是按 `.env.example` 的说明在仓库根建了 `.env`、
    #: 改了 `ORDERDELTA_DATA_DIR`，启动后数据照旧写进默认目录，且没有任何提示。
    #: 配置「看起来生效了其实没生效」比配置项不存在更难查。
    model_config = SettingsConfigDict(
        env_prefix="ORDERDELTA_", env_file=REPO_ROOT / ".env", extra="ignore"
    )

    #: 监听地址。**默认回环**，这是本 MVP 唯一的访问控制手段。
    host: str = "127.0.0.1"
    port: int = 8000

    #: 数据目录。用户要能在资源管理器里直接找到并删除自己的数据。
    data_dir: Path = REPO_ROOT / "data"

    #: 单个上传文件大小上限（字节）。
    max_upload_bytes: int = 20 * 1024 * 1024

    #: 只接受这些扩展名。MVP-0 不支持 PDF。
    allowed_suffixes: tuple[str, ...] = (".xlsx",)

    #: 允许的 MIME（扩展名 + MIME 双重检查，SPEC §15.1）。
    allowed_mime_types: tuple[str, ...] = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/octet-stream",  # 部分浏览器/客户端不给准确 MIME
    )

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "orderdelta.sqlite3"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.db_path.as_posix()}"

    def ensure_dirs(self) -> None:
        self.files_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
