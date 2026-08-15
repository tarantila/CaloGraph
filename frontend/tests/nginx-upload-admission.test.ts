import { readFile } from 'node:fs/promises'

import { describe, expect, it } from 'vitest'

const config = await readFile('nginx.conf', 'utf8')

describe('Apple-Health-Upload-Admission', () => {
  it('enforces independent per-client and global connection limits', () => {
    expect(config).toContain(
      'limit_conn_zone $binary_remote_addr zone=large_uploads_per_client:10m;',
    )
    expect(config).toContain('limit_conn_zone $server_name zone=large_uploads_global:10m;')
    expect(config).toContain(
      'limit_conn large_uploads_per_client 1;\n      limit_conn large_uploads_global 2;',
    )
    expect(config).not.toContain('limit_conn large_uploads_global 1;')
  })

  it('uses the generated trusted Real-IP result for forwarded requests', () => {
    expect(config).toContain('include /tmp/calograph-real-ip.conf;')
    expect(config).toContain('proxy_set_header X-Forwarded-For $remote_addr;')
  })
})
