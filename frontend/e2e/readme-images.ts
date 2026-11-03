// Files `npm run capture:readme` writes to docs/img/ (see e2e/capture/).
export const README_IMAGES = {
  upload: 'upload.png',
  answer: 'answer.png',
  citation: 'citation.png',
  gif: 'demo.gif',
} as const

export const MAX_IMAGE_BYTES = 5 * 1024 * 1024
