export interface RecentChat { key: string; firstQuestion: string; ts: number }

const storageKey = (app: string) => `docs-wiki.recent-chats.${app}`;
const CAP = 50;

export function listRecent(app: string): RecentChat[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(storageKey(app)) ?? "[]");
    return Array.isArray(parsed) ? parsed.filter(
      (e): e is RecentChat => typeof e?.key === "string" && typeof e?.firstQuestion === "string",
    ) : [];
  } catch {
    return [];
  }
}

export function addRecent(app: string, entry: RecentChat): void {
  const rest = listRecent(app).filter((e) => e.key !== entry.key);
  localStorage.setItem(storageKey(app), JSON.stringify([entry, ...rest].slice(0, CAP)));
}

export function removeRecent(app: string, key: string): void {
  localStorage.setItem(storageKey(app),
    JSON.stringify(listRecent(app).filter((e) => e.key !== key)));
}
