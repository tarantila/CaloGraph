import { readdir, readFile } from 'node:fs/promises'
import { join, relative, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const sourceRoot = resolve(process.cwd(), 'src')
const hardcodedVersionPattern = /CaloGraph v\d+\.\d+\.\d+/

async function sourceFiles(directory: string): Promise<string[]> {
  const entries = await readdir(directory, { withFileTypes: true })
  const files = await Promise.all(entries.map(async (entry) => {
    const path = join(directory, entry.name)
    return entry.isDirectory() ? sourceFiles(path) : [path]
  }))
  return files.flat()
}

describe('Frontend-Versionierungsrichtlinie', () => {
  it('verbietet hartcodierte CaloGraph-SemVer im produktiven Source', async () => {
    const violations = await Promise.all((await sourceFiles(sourceRoot)).map(async (path) => {
      const content = await readFile(path, 'utf8')
      return hardcodedVersionPattern.test(content) ? relative(sourceRoot, path) : null
    }))

    expect(violations.filter((path): path is string => path != null)).toEqual([])
  })
})
