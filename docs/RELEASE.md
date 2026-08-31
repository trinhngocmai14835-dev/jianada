# Windows 打包与发布

## 前置条件

正式包必须使用支持 CI/CD 的 PyArmor 商业许可证。不要把注册文件提交到仓库。在 GitHub 仓库 Actions secrets 中创建：

- `PYARMOR_CI_REGFILE_BASE64`：PyArmor CI 注册 ZIP 的 Base64 内容。

本地可通过 `pyarmor reg -C pyarmor-regfile-xxxx.zip` 申请 CI 注册文件；在 CI 中由工作流恢复并注册。

## 构建发布候选包

1. 更新 `backend/core/version.py` 中的 `APP_VERSION`。
2. 运行 `release.bat`，或在 GitHub Actions 手动运行 `Windows package`。
3. 得到 `release_upload/autobet-pro-<version>.zip` 与 `release_upload/latest.json`。
4. 检查 EXE、版本、说明和 SHA-256。

试用/非商业 PyArmor 默认会中止打包。仅调试时可设置 `ALLOW_TRIAL_PYARMOR=1`；未加密构建还必须显式设置 `ALLOW_UNOBFUSCATED_BUILD=1`。

## 发布到 R2

配置 Cloudflare 凭据和 Wrangler 后运行：

```bat
publish_release.bat
```

脚本会先上传 ZIP，最后上传 `latest.json`，避免客户端看到尚不存在的下载包。发布操作必须输入 `PUBLISH` 确认。
