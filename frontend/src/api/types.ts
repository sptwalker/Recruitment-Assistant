// 后端响应类型（对齐 backend/app/routers + auth）。仅列前端用到的字段。

export interface Me {
  id: number;
  org_id: number | null;
  name: string | null;
  avatar_url: string | null;
  email: string | null;
  role: "admin" | "manager" | "recruiter";
}

export interface Candidate {
  candidate_id: number;
  name: string;
  gender: string | null;
  age: number | null;
  phone: string | null;
  email: string | null;
  current_city: string | null;
  education_level: string | null;
  create_time: string | null;
  update_time: string | null;
}

export interface CandidatePage {
  total: number;
  items: Candidate[];
}

export interface Job {
  id: number;
  status: string;
  title: string;
  department: string | null;
  work_city: string | null;
  salary_range: string | null;
  min_education: string | null;
  min_experience: string | null;
  responsibilities: string | null;
  job_requirements: string | null;
  required_skills: string[] | null;
  preferred_skills: string[] | null;
  description: string | null;
  source_file_name: string | null;
}

export interface JobMatch {
  candidate_id: number;
  name: string;
  score: number;
  reason: string;
  skill_match: number | null;
  experience_match: number | null;
  education_match: number | null;
  location_match: number | null;
}

// 日志/AI 用量的行是中文键的松散字典，直接透传
export type LogRow = Record<string, string | number>;

export interface LogsResponse {
  items: LogRow[];
  summary: Record<string, number>;
}
