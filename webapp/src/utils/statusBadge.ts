export function statusBadge(status: string): string {
  switch (status) {
    case "succeeded":
      return "bg-emerald-100 text-emerald-700";
    case "failed":
      return "bg-red-100 text-red-700";
    default:
      return "";
  }
}
