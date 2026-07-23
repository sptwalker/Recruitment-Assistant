import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type {
  Candidate,
  CandidatePage,
  Job,
  JobMatch,
  LogsResponse,
  Me,
} from "./types";

export interface JobPositionCreateBody {
  title: string;
  department?: string;
  work_city?: string;
  salary_range?: string;
  min_education?: string;
  min_experience?: string;
  responsibilities?: string;
  job_requirements?: string;
  description?: string;
}

export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: async () => (await api.get<Me>("/auth/me")).data,
    retry: false,
    staleTime: 5 * 60 * 1000,
  });
}

export interface CandidateFilters {
  page: number;
  page_size: number;
  name?: string;
  city?: string;
}

export function useCandidates(filters: CandidateFilters) {
  return useQuery({
    queryKey: ["candidates", filters],
    queryFn: async () =>
      (await api.get<CandidatePage>("/candidates", { params: filters })).data,
    placeholderData: (prev) => prev,
  });
}

export function useDeleteCandidate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: number) => api.delete(`/candidates/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["candidates"] }),
  });
}

export function useJobs(keyword?: string) {
  return useQuery({
    queryKey: ["jobs", keyword ?? ""],
    queryFn: async () =>
      (await api.get<Job[]>("/jobs", { params: { keyword } })).data,
  });
}

export function useCreateJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: JobPositionCreateBody) =>
      (await api.post<Job>("/jobs", body)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useDeleteJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: number) => api.delete(`/jobs/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useJobMatches(positionId: number | null) {
  return useQuery({
    queryKey: ["job-matches", positionId],
    enabled: positionId != null,
    queryFn: async () =>
      (await api.get<JobMatch[]>(`/jobs/${positionId}/matches`)).data,
  });
}

export function useOperationLogs(day: string, enabled = true) {
  return useQuery({
    queryKey: ["logs-ops", day],
    enabled,
    queryFn: async () =>
      (await api.get<LogsResponse>("/logs/operations", { params: { day } })).data,
  });
}

export function useAiUsage(day: string, enabled = true) {
  return useQuery({
    queryKey: ["logs-ai", day],
    enabled,
    queryFn: async () =>
      (await api.get<LogsResponse>("/logs/ai-usage", { params: { day } })).data,
  });
}

export interface CrawlStatus {
  connected: boolean;
  platforms: string[];
}

export function useCrawlStatus() {
  return useQuery({
    queryKey: ["crawl-status"],
    queryFn: async () => (await api.get<CrawlStatus>("/crawl/status")).data,
    refetchInterval: 5000, // 轮询扩展在线状态
  });
}

export function useCrawlCommand() {
  return useMutation({
    mutationFn: async (command: Record<string, unknown>) =>
      (await api.post<{ delivered: number }>("/crawl/command", { command })).data,
  });
}

export function useCrawlToken() {
  return useMutation({
    mutationFn: async () => (await api.get<{ token: string }>("/crawl/token")).data,
  });
}

// 重新导出便于页面 import 类型
export type { Candidate, Job, JobMatch };
