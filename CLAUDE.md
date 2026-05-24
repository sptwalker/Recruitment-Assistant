# 全局规则：自动委派大输出任务给子代理
When you need to:
- Read a file > 500 lines
- Grep/Glob with many results
- Run Bash commands with large output
- Run test suites or long builds
Delegate automatically to the large-task-handler subagent.
Return only summarized results to main conversation.
