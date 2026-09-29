/**
 * Anything a conditional class expression can produce. `bigint` and `boolean`
 * are included because `items.length && 'x'` and `cond ? 'a' : 'b'` are both
 * legal TypeScript and both appear in component code.
 */
export type ClassValue = string | number | bigint | boolean | null | undefined

/**
 * Class-name joiner.
 *
 * Accepts the falsy values that actually show up in conditional class lists
 * (`cond && 'x'`, `{items.length > 0 && 'y'}`) and drops them, so callers never
 * have to wrap a condition in a ternary just to satisfy the type checker.
 *
 * Only strings survive. A number or boolean that reached here by accident is
 * dropped rather than emitted as the class `0` or `true`.
 */
export function cn(...values: ClassValue[]): string {
  return values.filter((value): value is string => typeof value === 'string' && value.length > 0).join(' ')
}
