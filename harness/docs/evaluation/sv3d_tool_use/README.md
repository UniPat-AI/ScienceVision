# sv3d Tool-Use 测试

## 跑测试
pytest tests/mcp/test_sv3d_tool_use/ -q

## 跑 Claude Code 剧本
1. 启动本地 viewer: python tools/serve_viewer.py --port 8000
2. 在 Claude Code 里粘贴 docs/evaluation/sv3d_tool_use/scenarios.md 的某段
3. 拷贝 Claude Code 输出的工具调用到 evaluation_runs/<run_id>/tool_calls.jsonl
4. python scripts/eval/sv3d_run_scenarios.py --tool-calls evaluation_runs/<run_id>/tool_calls.jsonl --out evaluation_runs/<run_id>/
5. 打开 evaluation_runs/<run_id>/viewer_urls.txt 第一行,肉眼比对

## V1 限制
浏览器 → MCP 不双向同步: 在 viewer 里手动旋转不会改 Session 状态。
本测试只验证 MCP → viewer 的单向 single-shot 对齐。
