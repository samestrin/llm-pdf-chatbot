// A stand-in for the backend, used only to test the dev server's `/api` proxy.
// It answers the backend's own paths (no `/api` prefix), so a request that reaches
// it still carrying `/api` gets a 404 and the proxy test fails.
import { createServer } from 'node:http'

const port = Number(process.argv[2] ?? 5198)

createServer((req, res) => {
  const body =
    req.method === 'GET' && req.url === '/health'
      ? { status: 'ok', server: 'fake-backend' }
      : { detail: `fake-backend has no route for ${req.method} ${req.url}` }
  res.writeHead(body.status ? 200 : 404, { 'Content-Type': 'application/json' })
  res.end(JSON.stringify(body))
}).listen(port, '127.0.0.1')
