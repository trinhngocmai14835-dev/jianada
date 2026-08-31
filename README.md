# jianada

自动下单系统 Pro。项目采用前后端分离结构：FastAPI 后端、React/Vite 前端，并提供 Windows 单文件 EXE 打包与 R2 更新发布流程。

## 快速开始

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cd frontend && npm ci && cd ..
pytest -q
```

开发、测试、打包与发布说明见 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) 和 [docs/RELEASE.md](docs/RELEASE.md)。

## 自动化

- Pull Request / `main` 推送：运行 Python 测试、依赖检查、前端审计与生产构建。
- Windows 打包：在 GitHub Actions 手动启动，使用 PyArmor CI 注册文件生成可下载的发布候选包。
- 正式发布：必须在本地显式确认后上传 ZIP，校验成功后最后更新 `latest.json`。
