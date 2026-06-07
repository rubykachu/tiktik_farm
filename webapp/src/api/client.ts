import axios from "axios";

export const api = axios.create({
  baseURL: "/api",
  headers: { "Content-Type": "application/json" },
});

export interface Account {
  id: number;
  username: string;
  display_name: string | null;
  cookie_path: string;
  has_valid_session: boolean;
  created_at: string;
  updated_at: string;
  last_used_at: string | null;
  login: string | null;
  login_password: string | null;
}

export interface VideoFile {
  name: string;
  size_bytes: number;
  modified_at: string;
}

export const Accounts = {
  list: () => api.get<Account[]>("/accounts").then(r => r.data),
  remove: (id: number) => api.delete(`/accounts/${id}`),
  importJson: (
    file: File,
    username: string,
    replace: boolean,
    login?: string,
    login_password?: string,
  ) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("username", username);
    if (replace) fd.append("replace", "true");
    if (login) fd.append("login", login);
    if (login_password) fd.append("login_password", login_password);
    return api
      .post<Account>("/accounts/import-json", fd, {
        headers: { "Content-Type": "multipart/form-data" },
      })
      .then(r => r.data);
  },
  update: (id: number, payload: { display_name?: string; login?: string; login_password?: string }) =>
    api.patch<Account>(`/accounts/${id}`, payload).then(r => r.data),
};

export const Videos = {
  list: () => api.get<VideoFile[]>("/videos").then(r => r.data),
};

