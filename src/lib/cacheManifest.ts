export type CacheStats = { ready: number; queued: number; reused: number }
/** Stable cache keys prevent regenerating a derivative while its source and renderer remain unchanged. */
export function createCacheKey(mediaIdentity: string, sourceSignature: string, derivativeVersion: string) { return `${compactHash(`${mediaIdentity}|${sourceSignature}|${derivativeVersion}`)}-${derivativeVersion}` }
function compactHash(value: string) { let hash = 2166136261; for (let index = 0; index < value.length; index += 1) { hash ^= value.charCodeAt(index); hash = Math.imul(hash, 16777619) } return (hash >>> 0).toString(36) }
