import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Hostnames that mean "the machine doing the build". Kept deliberately in
// step with `_LOCAL_HOSTS` in the backend's app/config.py, which answers
// the same question about CORS_ORIGINS.
const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '0.0.0.0', '[::1]', '::1', ''])

function hostOf(url) {
  // Hand-rolled rather than `new URL()`, which throws on a schemeless
  // "localhost:8000" -- exactly the value this needs to recognise.
  let s = String(url).trim().toLowerCase()
  const i = s.indexOf('://')
  if (i !== -1) s = s.slice(i + 3)
  s = s.split('/')[0]
  const at = s.lastIndexOf('@')
  if (at !== -1) s = s.slice(at + 1)
  if (s.startsWith('[')) return s.slice(0, s.indexOf(']') + 1)
  return s.split(':')[0]
}

/**
 * Refuse to build a bundle that points at the machine that built it.
 *
 * `VITE_API_URL` is compiled into the JavaScript, not read at runtime, so
 * a build run without it ships a dashboard hardcoded to
 * http://localhost:8000. Deployed, every request goes to the *visitor's*
 * own machine and fails -- which looks like a login that does nothing,
 * not like an error. That is the same failure the backend's CORS check
 * refuses to start on, from the other side of the same connection, and
 * the backend cannot see it: by the time the bundle is wrong, nothing
 * server-side is involved.
 *
 * Two things are deliberately exempt:
 *
 *   - the SSR build behind `npm run smoke`, which renders pages to a
 *     string and never issues a request, so the value is unused;
 *   - anyone who sets ALLOW_LOCAL_API_URL=true, for `npm run build &&
 *     npm run preview` against a local API. Named after the backend's
 *     ALLOW_MULTIPLE_WORKERS, which is the same shape of escape hatch:
 *     it suppresses the check without making anything safe.
 */
function checkApiUrl(env, isSsrBuild) {
  const configured = env.VITE_API_URL || ''
  const effective = configured || 'http://localhost:8000'
  const local = LOCAL_HOSTS.has(hostOf(effective))

  if (isSsrBuild) return

  if (local && env.ALLOW_LOCAL_API_URL !== 'true') {
    throw new Error(
      '\n\n' +
        '  Refusing to build: VITE_API_URL is ' +
        (configured ? `"${configured}"` : 'unset, so it defaults to "http://localhost:8000"') +
        '.\n\n' +
        '  This value is compiled into the bundle, not read at runtime. Deployed as\n' +
        "  it is, every API call would go to the visitor's own machine and fail --\n" +
        '  which presents as a login that silently does nothing.\n\n' +
        '  Set VITE_API_URL to the public URL of the backend, and make sure that\n' +
        "  same URL is in the backend's CORS_ORIGINS.\n\n" +
        '  Building for local preview? ALLOW_LOCAL_API_URL=true npm run build\n'
    )
  }

  // Printed on every real build, including a correct one. The value that
  // went into the bundle is not visible anywhere else, and a deployer
  // reading a CI log should not have to unzip a chunk to find out.
  console.log(`\n  vite: API URL compiled into this bundle -> ${effective}\n`)
}

export default defineConfig(({ command, mode, isSsrBuild }) => {
  // loadEnv, not process.env: Vite reads .env / .env.production itself,
  // and a check that only saw shell variables would pass a build whose
  // bundle was configured by a file.
  const env = loadEnv(mode, process.cwd(), '')
  if (command === 'build') checkApiUrl(env, isSsrBuild)

  return {
    plugins: [react(), tailwindcss()],
  }
})
