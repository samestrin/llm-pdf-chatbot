// A stand-in for the backend, used to test the dev server's `/api` proxy and to
// stream fake chat answers through it. It answers the backend's own paths (no
// `/api` prefix), so a request that reaches it still carrying `/api` gets a 404
// and the proxy test fails.
//
// Streamed chat (e2e/chat.spec.ts): a test scripts an answer with
// `PUT /_fake/chat/{key}` and body `{ batches: [[event, ...], ...] }`. A chat
// request whose question is `key` gets the first batch as NDJSON at once; each
// `POST /_fake/chat/{key}/next` writes the next batch, and the body ends after
// the last one. The key keeps parallel tests apart on the one server.
//
// `GET /_fake/request` answers with the path it was asked for and the
// X-Forwarded-Prefix header it received, so a test can see what the proxy sends.
import { createServer } from 'node:http'

const port = Number(process.argv[2] ?? 5198)

/** key -> { batches, sent, res } */
const scripts = new Map()

function readJson(req) {
  return new Promise((resolve, reject) => {
    let data = ''
    req.on('data', (c) => (data += c))
    req.on('end', () => {
      try {
        resolve(JSON.parse(data || '{}'))
      } catch (err) {
        reject(err)
      }
    })
  })
}

function send(res, status, body) {
  res.writeHead(status, { 'Content-Type': 'application/json' })
  res.end(JSON.stringify(body))
}

function writeNext(script) {
  const batch = script.batches[script.sent++]
  for (const event of batch) script.res.write(`${JSON.stringify(event)}\n`)
  if (script.sent >= script.batches.length) script.res.end()
}

createServer(async (req, res) => {
  const { pathname } = new URL(req.url, 'http://fake')
  let m
  if (req.method === 'GET' && pathname === '/health') {
    return send(res, 200, { status: 'ok', server: 'fake-backend' })
  }
  if (req.method === 'GET' && pathname === '/_fake/request') {
    // What the proxy forwarded: the path it sent and the prefix header it added.
    return send(res, 200, { path: pathname, forwardedPrefix: req.headers['x-forwarded-prefix'] ?? null })
  }
  if (req.method === 'PUT' && (m = pathname.match(/^\/_fake\/chat\/([^/]+)$/))) {
    const { batches } = await readJson(req)
    scripts.set(decodeURIComponent(m[1]), { batches, sent: 0, res: null })
    return send(res, 200, { ok: true })
  }
  if (req.method === 'POST' && (m = pathname.match(/^\/_fake\/chat\/([^/]+)\/next$/))) {
    const script = scripts.get(decodeURIComponent(m[1]))
    if (!script?.res || script.sent >= script.batches.length) {
      return send(res, 409, { detail: 'no open stream with batches left' })
    }
    writeNext(script)
    return send(res, 200, { ok: true })
  }
  if (req.method === 'POST' && /^\/collections\/[^/]+\/chat$/.test(pathname)) {
    const { question } = await readJson(req)
    const script = scripts.get(question)
    if (!script) return send(res, 404, { detail: `no scripted answer for ${question}` })
    script.res = res
    res.writeHead(200, { 'Content-Type': 'application/x-ndjson', 'Cache-Control': 'no-cache' })
    return writeNext(script)
  }
  send(res, 404, { detail: `fake-backend has no route for ${req.method} ${req.url}` })
}).listen(port, '127.0.0.1')
