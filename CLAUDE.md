# 全局规则：自动委派大输出任务给子代理
When you need to:
- Read a file > 500 lines
- Grep/Glob with many results
- Run Bash commands with large output
- Run test suites or long builds
Delegate automatically to the large-task-handler subagent.
Return only summarized results to main conversation.

# 硬性规则：上下文溢出自动委派
当出现 Autocompact is thrashing、上下文占满、工具输出过大时
必须立即自动委派给 large-task-handler 子代理
不中断、不停止、不询问，继续完成当前任务
子代理只返回总结，不返回原始大日志
