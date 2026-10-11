export type IconName = 'library' | 'recent' | 'favorites' | 'folders' | 'people' | 'places' | 'duplicates' | 'image' | 'video' | 'storage' | 'search' | 'plus' | 'menu' | 'close' | 'zoomIn' | 'zoomOut' | 'fit' | 'fullscreen' | 'chevron' | 'backup' | 'restore' | 'shield' | 'phone' | 'settings'
const paths: Record<IconName, string> = {
  settings:'M4 7h16M4 17h16M8 4v6M16 14v6',
  backup:'M4 4h16v16H4zM4 15h5l2 3h2l2-3h5M12 12V5M9 8l3-3 3 3',
  restore:'M3 8V3h5M3 8a9 9 0 1 1 0 8M12 8v5l3 2',
  shield:'M12 3 3 7v6c0 5 9 9 9 9s9-4 9-9V7ZM8 12l3 3 5-6',
  phone:'M7 2h10a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2ZM10 5h4M12 19h.01',
  library: 'M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z',
  recent: 'M12 8v5l3 2M3.5 7A9 9 0 1 1 3 13M3 3v5h5',
  favorites: 'M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z',
  folders: 'M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2ZM3 9h18',
  people: 'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M16 3a4 4 0 0 1 0 8M22 21v-2a4 4 0 0 0-3-3.9M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z',
  places: 'M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 1 1 16 0ZM15 10a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z',
  duplicates: 'M8 8h12v12H8zM16 4H4v12',
  image: 'M3 3h18v18H3zM3 16l5-5 5 5 3-3 5 5M16 7h.01',
  video: 'M3 5h13v14H3zM16 9l5-3v12l-5-3',
  storage: 'M3 5c0-3 18-3 18 0s-18 3-18 0ZM3 5v14c0 3 18 3 18 0V5M3 12c0 3 18 3 18 0',
  search: 'M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z',
  plus: 'M12 5v14M5 12h14', menu: 'M4 6h16M4 12h16M4 18h16',
  close: 'M6 6l12 12M18 6 6 18', zoomIn: 'M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0ZM10 6v8M6 10h8',
  zoomOut: 'M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0ZM6 10h8',
  fit: 'M8 3H3v5M16 3h5v5M3 16v5h5M21 16v5h-5',
  fullscreen: 'M3 8V3h5M16 3h5v5M3 16v5h5M21 16v5h-5', chevron: 'm9 5 7 7-7 7',
}
export function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>
}
