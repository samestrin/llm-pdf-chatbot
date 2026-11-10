import { useCallback, useState } from 'react'

export const DEFAULT_COLLECTION = 'default'

/** The one collection that uploads go into and questions are asked in. */
export interface ActiveCollection {
  name: string
  /** A new name being typed, not yet a collection: the next upload creates it. */
  draft: boolean
}

/**
 * The active collection once the collections are (re)loaded: a typed name that now exists
 * stops being a draft, and a chosen collection that is gone (its last document was deleted)
 * falls back to the first collection, else `default`. Returns `active` itself when nothing
 * changes.
 */
export function settle(active: ActiveCollection, names: string[]): ActiveCollection {
  if (active.draft) {
    const name = active.name.trim()
    return name && names.includes(name) ? { name, draft: false } : active
  }
  if (names.includes(active.name)) return active
  return { name: names[0] ?? DEFAULT_COLLECTION, draft: false }
}

export function useActiveCollection() {
  const [active, setActive] = useState<ActiveCollection>({
    name: DEFAULT_COLLECTION,
    draft: false,
  })
  const onLoaded = useCallback((names: string[]) => setActive((a) => settle(a, names)), [])
  return { active, setActive, onLoaded }
}
