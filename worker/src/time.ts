/**
 * Time helpers.
 *
 * Daily buckets are Asia/Kolkata calendar dates. A stats page that splits days at
 * 05:30 local is wrong forever after, and the mistake is invisible until you look
 * at a late-night reading session and find it filed under tomorrow.
 *
 * IST is UTC+5:30 year-round with no DST, so a fixed offset is exact rather than
 * an approximation — this is one of the few timezones where that is true.
 */

export const IST_OFFSET_SECONDS = 5.5 * 3600; // 19800

/** Unix seconds -> 'YYYY-MM-DD' on the IST calendar. */
export function istDay(unixSeconds: number): string {
  return new Date((unixSeconds + IST_OFFSET_SECONDS) * 1000).toISOString().slice(0, 10);
}

/** 'YYYY-MM-DD' (IST calendar) -> unix seconds at 00:00:00 IST that day. */
export function istDayStart(day: string): number {
  return Math.floor(Date.parse(`${day}T00:00:00Z`) / 1000) - IST_OFFSET_SECONDS;
}

/** Days between two IST calendar dates, positive when `b` is later. */
export function daysBetween(a: string, b: string): number {
  return Math.round((istDayStart(b) - istDayStart(a)) / 86400);
}

export function nowSeconds(): number {
  return Math.floor(Date.now() / 1000);
}
