import { readFile } from 'node:fs/promises'

import { describe, expect, it } from 'vitest'

const config = await readFile('nginx.conf', 'utf8')

describe('Apple-Health-Upload-Admission', () => {
  it('reserves exactly one global connection slot for the bounded backend spool', () => {
    expect(config).toContain('limit_conn_zone $server_name zone=large_uploads_global:10m;')
    expect(config).toContain('location = /api/v1/import/apple-health/file {\n      include /tmp/calograph-upload-limit.conf;\n      limit_conn large_uploads_global 1;')
    expect(config).not.toContain('limit_conn_zone $binary_remote_addr zone=large_uploads_per_client:10m;')
  })
})
