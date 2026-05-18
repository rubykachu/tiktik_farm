import { api } from "./client";

export interface DouyinCookie {
  id: number;
  name: string;
  created_at: string;
  updated_at: string;
}

export const DouyinCookies = {
  list: () => api.get<DouyinCookie[]>("/douyin-cookies").then((r) => r.data),
  create: (file: File, name: string) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("name", name);
    return api
      .post<DouyinCookie>("/douyin-cookies", fd, {
        headers: { "Content-Type": "multipart/form-data" },
      })
      .then((r) => r.data);
  },
  update: (id: number, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return api
      .put<DouyinCookie>(`/douyin-cookies/${id}`, fd, {
        headers: { "Content-Type": "multipart/form-data" },
      })
      .then((r) => r.data);
  },
  remove: (id: number) => api.delete(`/douyin-cookies/${id}`),
};
