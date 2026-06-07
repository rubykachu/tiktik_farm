import { api } from "./client";

export interface DouyinCookie {
  id: number;
  name: string;
  created_at: string;
  updated_at: string;
  login: string | null;
  login_password: string | null;
}

export const DouyinCookies = {
  list: () => api.get<DouyinCookie[]>("/douyin-cookies").then((r) => r.data),
  create: (file: File, name: string, login?: string, login_password?: string) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("name", name);
    if (login) fd.append("login", login);
    if (login_password) fd.append("login_password", login_password);
    return api
      .post<DouyinCookie>("/douyin-cookies", fd, {
        headers: { "Content-Type": "multipart/form-data" },
      })
      .then((r) => r.data);
  },
  update: (id: number, opts: { file?: File; name?: string; login?: string; login_password?: string }) => {
    const fd = new FormData();
    if (opts.file) fd.append("file", opts.file);
    if (opts.name !== undefined) fd.append("name", opts.name);
    if (opts.login !== undefined) fd.append("login", opts.login);
    if (opts.login_password !== undefined) fd.append("login_password", opts.login_password);
    return api
      .put<DouyinCookie>(`/douyin-cookies/${id}`, fd, {
        headers: { "Content-Type": "multipart/form-data" },
      })
      .then((r) => r.data);
  },
  remove: (id: number) => api.delete(`/douyin-cookies/${id}`),
};
