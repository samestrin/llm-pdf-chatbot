// Writes sample.pdf: a two-page PDF with known text for the browser tests.
// The PDF is committed; rerun this only to change it: `node e2e/fixtures/make-sample-pdf.mjs`.
import { writeFileSync } from 'node:fs'

export const PAGES = [
  'The quick brown fox jumps over the lazy dog.',
  'Revenue grew twelve percent in the third quarter.',
]

const objects = []
const add = (body) => objects.push(body) // object number = index + 1

add('<< /Type /Catalog /Pages 2 0 R >>')
add('') // pages, filled in below
add('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')
const kids = []
for (const text of PAGES) {
  const stream = `BT /F1 18 Tf 72 720 Td (${text}) Tj ET`
  add(`<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`)
  add(
    `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] ` +
      `/Resources << /Font << /F1 3 0 R >> >> /Contents ${objects.length} 0 R >>`,
  )
  kids.push(`${objects.length} 0 R`)
}
objects[1] = `<< /Type /Pages /Kids [${kids.join(' ')}] /Count ${kids.length} >>`

let out = '%PDF-1.4\n'
const offsets = []
objects.forEach((body, i) => {
  offsets.push(out.length)
  out += `${i + 1} 0 obj\n${body}\nendobj\n`
})
const xref = out.length
out += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`
for (const offset of offsets) out += `${String(offset).padStart(10, '0')} 00000 n \n`
out += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`

writeFileSync(new URL('./sample.pdf', import.meta.url), out, 'latin1')
