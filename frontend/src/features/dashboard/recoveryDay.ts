/**
 * The recovery day is DERIVED from the patient's real discharge date; nothing
 * is fabricated. The API has no planned-recovery-duration field, so a
 * percentage and "remaining days" cannot be computed honestly — consumers show
 * a neutral state instead of inventing a denominator.
 *
 * `now` is passed in rather than read here so every caller on a page observes
 * the same pinned instant (see the dashboard and RecoveryProgressCard).
 */
export function recoveryDay(dischargeDate: string | null, now: number): number | null {
  if (!dischargeDate) return null
  const start = Date.parse(dischargeDate)
  if (Number.isNaN(start)) return null
  return Math.max(1, Math.floor((now - start) / 86_400_000) + 1)
}