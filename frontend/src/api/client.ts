import axios from "axios";

// 同源（经 dev 代理 / 生产 nginx 反代到后端），带上 httpOnly cookie。
export const api = axios.create({ baseURL: "/", withCredentials: true });

// 401 → 跳登录页（避免在受保护页面里静默失败）。不自动跳飞书，防重定向环。
api.interceptors.response.use(
  (r) => r,
  (error) => {
    if (error?.response?.status === 401 && window.location.pathname !== "/login") {
      window.location.assign("/login");
    }
    return Promise.reject(error);
  }
);
