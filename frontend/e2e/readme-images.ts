// The README's images in docs/img/: `npm run capture:readme` writes the PNGs from the
// fixture-backed page (e2e/capture/); `npm run capture:live` records the GIF from a
// running instance (e2e/live/).
export const README_IMAGES = {
  upload: 'upload.png',
  answer: 'answer.png',
  citation: 'citation.png',
  dark: 'dark.png',
  phone: 'phone.png',
  gif: 'demo.gif',
} as const

export const MAX_IMAGE_BYTES = 5 * 1024 * 1024
