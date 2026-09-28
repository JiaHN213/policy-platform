# API 合约

`schema.yaml` 从 Django DRF 生成；前端 `src/lib/schema.d.ts` 从该文件生成。

```powershell
.venv/Scripts/python.exe apps/api/manage.py spectacular --settings=config.test_settings --file packages/api-contract/schema.yaml --validate --fail-on-warn
pnpm api:types
```

生产尚未实现的 AI 公共接口、支付、外部通知等不加入当前 schema。
