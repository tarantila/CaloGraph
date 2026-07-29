export interface WebAuthnOptionsResponse {
  challenge_id: string
  public_key: Record<string, unknown>
}

export type WebAuthnCredentialJSON =
  | AuthenticationResponseJSON
  | RegistrationResponseJSON

interface PublicKeyCredentialConstructorWithParsers {
  parseCreationOptionsFromJSON?: (
    options: Record<string, unknown>,
  ) => PublicKeyCredentialCreationOptions
  parseRequestOptionsFromJSON?: (
    options: Record<string, unknown>,
  ) => PublicKeyCredentialRequestOptions
}

export function isPasskeySupported(): boolean {
  return (
    globalThis.isSecureContext === true &&
    typeof globalThis.PublicKeyCredential !== 'undefined' &&
    typeof navigator !== 'undefined' &&
    Boolean(navigator.credentials)
  )
}

export async function createPasskey(
  options: Record<string, unknown>,
): Promise<WebAuthnCredentialJSON> {
  requirePasskeySupport()
  const credential = await navigator.credentials.create({
    publicKey: parseCreationOptions(options),
  })
  if (!(credential instanceof PublicKeyCredential)) {
    throw new Error('Passkey-Erstellung wurde abgebrochen.')
  }
  return credentialToJSON(credential)
}

export async function authenticateWithPasskey(
  options: Record<string, unknown>,
): Promise<WebAuthnCredentialJSON> {
  requirePasskeySupport()
  const credential = await navigator.credentials.get({
    publicKey: parseRequestOptions(options),
  })
  if (!(credential instanceof PublicKeyCredential)) {
    throw new Error('Passkey-Anmeldung wurde abgebrochen.')
  }
  return credentialToJSON(credential)
}

function requirePasskeySupport(): void {
  if (!isPasskeySupported()) {
    throw new Error('Passkeys werden von diesem Browser oder dieser Verbindung nicht unterstützt.')
  }
}

function parseCreationOptions(
  options: Record<string, unknown>,
): PublicKeyCredentialCreationOptions {
  const constructor =
    PublicKeyCredential as unknown as PublicKeyCredentialConstructorWithParsers
  if (constructor.parseCreationOptionsFromJSON) {
    return constructor.parseCreationOptionsFromJSON(options)
  }
  const user = options.user as Record<string, unknown>
  const excludeCredentials = (options.excludeCredentials ?? []) as Array<
    Record<string, unknown>
  >
  return {
    ...options,
    challenge: decodeBase64url(String(options.challenge)),
    user: {
      ...user,
      id: decodeBase64url(String(user.id)),
    },
    excludeCredentials: excludeCredentials.map((credential) => ({
      ...credential,
      id: decodeBase64url(String(credential.id)),
    })),
  } as PublicKeyCredentialCreationOptions
}

function parseRequestOptions(
  options: Record<string, unknown>,
): PublicKeyCredentialRequestOptions {
  const constructor =
    PublicKeyCredential as unknown as PublicKeyCredentialConstructorWithParsers
  if (constructor.parseRequestOptionsFromJSON) {
    return constructor.parseRequestOptionsFromJSON(options)
  }
  const allowCredentials = (options.allowCredentials ?? []) as Array<
    Record<string, unknown>
  >
  return {
    ...options,
    challenge: decodeBase64url(String(options.challenge)),
    allowCredentials: allowCredentials.map((credential) => ({
      ...credential,
      id: decodeBase64url(String(credential.id)),
    })),
  } as PublicKeyCredentialRequestOptions
}

function credentialToJSON(
  credential: PublicKeyCredential,
): WebAuthnCredentialJSON {
  const nativeToJSON = (
    credential as PublicKeyCredential & {
      toJSON?: () => AuthenticationResponseJSON | RegistrationResponseJSON
    }
  ).toJSON
  if (nativeToJSON) return nativeToJSON.call(credential)

  const common = {
    id: credential.id,
    rawId: encodeBase64url(credential.rawId),
    authenticatorAttachment: credential.authenticatorAttachment,
    clientExtensionResults: credential.getClientExtensionResults(),
    type: 'public-key' as const,
  }
  if (credential.response instanceof AuthenticatorAttestationResponse) {
    return {
      ...common,
      response: {
        clientDataJSON: encodeBase64url(credential.response.clientDataJSON),
        attestationObject: encodeBase64url(credential.response.attestationObject),
        transports: credential.response.getTransports?.() ?? [],
      },
    } as RegistrationResponseJSON
  }
  if (credential.response instanceof AuthenticatorAssertionResponse) {
    return {
      ...common,
      response: {
        clientDataJSON: encodeBase64url(credential.response.clientDataJSON),
        authenticatorData: encodeBase64url(credential.response.authenticatorData),
        signature: encodeBase64url(credential.response.signature),
        userHandle: credential.response.userHandle
          ? encodeBase64url(credential.response.userHandle)
          : null,
      },
    } as AuthenticationResponseJSON
  }
  throw new Error('Der Browser hat eine unbekannte Passkey-Antwort geliefert.')
}

function decodeBase64url(value: string): ArrayBuffer {
  const padding = '='.repeat((4 - (value.length % 4)) % 4)
  const bytes = Uint8Array.from(
    atob(`${value.replaceAll('-', '+').replaceAll('_', '/')}${padding}`),
    (character) => character.charCodeAt(0),
  )
  return bytes.buffer
}

function encodeBase64url(value: ArrayBuffer): string {
  const bytes = new Uint8Array(value)
  let binary = ''
  for (const byte of bytes) binary += String.fromCharCode(byte)
  return btoa(binary).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/u, '')
}
