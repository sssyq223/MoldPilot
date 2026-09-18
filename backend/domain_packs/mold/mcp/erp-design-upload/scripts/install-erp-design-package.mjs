import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'

const source = process.env.MOLD_ERP_DESIGN_MCP_PACKAGE || process.argv[2]
if (!source) {
  console.error('Set MOLD_ERP_DESIGN_MCP_PACKAGE or pass the ERP MCP package/tarball path.')
  process.exit(2)
}
const packagePath = resolve(source)
if (!existsSync(packagePath)) {
  console.error(`ERP MCP package does not exist: ${packagePath}`)
  process.exit(2)
}
const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm'
const result = spawnSync(npm, ['install', '--no-save', packagePath], {
  cwd: new URL('..', import.meta.url),
  stdio: 'inherit',
  shell: false,
})
process.exit(result.status ?? 1)
