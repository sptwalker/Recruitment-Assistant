"""
智能匹配独立调试日志模块
每次匹配都会在 logs/match_debug/ 目录下生成一个独立的日志文件
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Any


class MatchDebugLogger:
    """匹配过程调试日志记录器"""

    def __init__(self, position_id: int, position_title: str):
        self.position_id = position_id
        self.position_title = position_title
        self.start_time = datetime.now()
        self.log_entries = []

        # 创建日志目录
        self.log_dir = Path("logs/match_debug")
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # 生成日志文件名
        timestamp = self.start_time.strftime("%Y%m%d_%H%M%S")
        self.log_file = self.log_dir / f"match_{position_id}_{timestamp}.json"

        self.log("init", "匹配调试日志开始", {
            "position_id": position_id,
            "position_title": position_title,
            "start_time": self.start_time.isoformat(),
        })

    def log(self, stage: str, message: str, data: dict[str, Any] | None = None):
        """记录一条日志"""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "stage": stage,
            "message": message,
        }
        if data:
            entry["data"] = data
        self.log_entries.append(entry)

    def log_candidates(self, candidates: list, candidate_dicts: list[dict]):
        """记录候选人列表信息"""
        ids = [c.candidate_id for c in candidates] if candidates else []
        self.log("candidates", f"读取候选人列表", {
            "total_count": len(candidates),
            "dict_count": len(candidate_dicts),
            "id_range": {
                "min": min(ids) if ids else None,
                "max": max(ids) if ids else None,
            },
            "first_10_ids": ids[:10],
            "last_10_ids": ids[-10:] if len(ids) > 10 else [],
            "sample_candidate": {
                "id": candidates[0].candidate_id if candidates else None,
                "name": candidates[0].name if candidates else None,
            } if candidates else None,
        })

    def log_ai_request(self, batch_index: int, candidate_count: int, jd_preview: str):
        """记录AI请求信息"""
        self.log("ai_request", f"发送AI请求 - 批次 {batch_index}", {
            "batch_index": batch_index,
            "candidate_count": candidate_count,
            "jd_preview": jd_preview[:200],
        })

    def log_ai_response(self, batch_index: int, results: list[dict]):
        """记录AI响应信息"""
        ids = [r.get("candidate_id") for r in results if "candidate_id" in r]
        self.log("ai_response", f"收到AI响应 - 批次 {batch_index}", {
            "batch_index": batch_index,
            "result_count": len(results),
            "returned_ids": ids,
            "sample_result": results[0] if results else None,
        })

    def log_save_attempt(self, total: int, candidate_ids: list[int]):
        """记录保存尝试"""
        self.log("save_attempt", f"尝试保存匹配结果", {
            "total_results": total,
            "id_range": {
                "min": min(candidate_ids) if candidate_ids else None,
                "max": max(candidate_ids) if candidate_ids else None,
            },
            "first_10_ids": candidate_ids[:10],
            "last_10_ids": candidate_ids[-10:] if len(candidate_ids) > 10 else [],
        })

    def log_save_result(self, save_ok: int, save_fail: int, failed_ids: list[int]):
        """记录保存结果"""
        self.log("save_result", f"保存完成", {
            "success_count": save_ok,
            "fail_count": save_fail,
            "failed_ids": failed_ids[:20],  # 只记录前20个失败ID
        })

    def log_error(self, stage: str, error: Exception):
        """记录错误"""
        self.log("error", f"错误: {stage}", {
            "error_type": type(error).__name__,
            "error_message": str(error),
        })

    def finalize(self):
        """完成日志记录并保存到文件"""
        end_time = datetime.now()
        duration = (end_time - self.start_time).total_seconds()

        summary = {
            "position_id": self.position_id,
            "position_title": self.position_title,
            "start_time": self.start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "duration_seconds": duration,
            "log_entries": self.log_entries,
        }

        # 保存到文件
        with open(self.log_file, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

        return str(self.log_file)
