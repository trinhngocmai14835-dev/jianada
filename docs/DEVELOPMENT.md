# 开发流程

## 环境

- Python 3.12
- Node.js 20
- Windows 10/11（仅 EXE 打包必需）

## 安装和验证

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pip check
pytest -q
cd frontend
npm ci
npm audit --omit=dev --audit-level=high
npm run build
```

日常修改从 `main` 创建短分支，完成测试后提交 Pull Request。CI 全部通过才合并。

当前 React Router 6 和 Vite 5 的部分公告需要分别升级到大版本才能彻底消除；CI 先阻止生产依赖中的 high/critical 问题，大版本迁移单独安排并回归测试。
