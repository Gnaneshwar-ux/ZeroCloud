import type { RuntimeMedia } from './runtimeMedia'
// Transparent, offline suggestions. Do not pretend filename clues are AI labels.
const clues: Record<string, RegExp> = {
 screenshots: /screenshot|screen[ _-]?shot|screen[ _-]?capture/i,
 beaches: /beach|seaside|coast|ocean|sea[ _-]|shore/i,
 landscapes: /landscape|scenery|mountain|valley|sunset|sunrise|panorama|forest|waterfall/i,
 food: /food|meal|lunch|dinner|breakfast|restaurant|cafe|coffee/i,
 documents: /receipt|invoice|document|scan[ _-]|certificate/i,
 animals: /cat|dog|pet|bird|animal|wildlife/i,
 architecture: /temple|church|castle|palace|building|architecture/i,
 plants: /flower|plant|garden|botanical/i,
}
export function categoryMatches(item: RuntimeMedia, category: string) {
 if (category === 'all' || category === 'date') return true
 if (item.categorySource === 'user') return item.categories?.includes(category) ?? false
 if (item.categories?.includes(category)) return true
 if (category === 'documents' && item.kind === 'document') return true
 return ['image','raw','video'].includes(item.kind) && !!clues[category]?.test(`${item.name} ${item.relativePath}`)
}
