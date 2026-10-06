import { supabase } from './supabase'

export const AVATAR_MAX_BYTES = 2 * 1024 * 1024
export const AVATAR_SIZE = 256
const ACCEPTED_TYPES = ['image/png', 'image/jpeg', 'image/webp']

export class AvatarError extends Error {}

function loadImage(file: File): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.onload = () => { URL.revokeObjectURL(url); resolve(img) }
    img.onerror = () => { URL.revokeObjectURL(url); reject(new AvatarError('avatar_invalid_image')) }
    img.src = url
  })
}

/** Center-crop to a square and downscale to AVATAR_SIZE, returning a webp blob. */
async function cropToSquareWebp(file: File): Promise<Blob> {
  const img = await loadImage(file)
  const side = Math.min(img.naturalWidth, img.naturalHeight)
  if (!side) throw new AvatarError('avatar_invalid_image')
  const sx = (img.naturalWidth - side) / 2
  const sy = (img.naturalHeight - side) / 2
  const canvas = document.createElement('canvas')
  canvas.width = AVATAR_SIZE
  canvas.height = AVATAR_SIZE
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new AvatarError('avatar_invalid_image')
  ctx.drawImage(img, sx, sy, side, side, 0, 0, AVATAR_SIZE, AVATAR_SIZE)
  const blob: Blob | null = await new Promise(resolve => canvas.toBlob(resolve, 'image/webp', 0.9))
  if (!blob) throw new AvatarError('avatar_invalid_image')
  return blob
}

function randomName(length = 16): string {
  const alphabet = 'abcdefghijklmnopqrstuvwxyz0123456789'
  const values = new Uint8Array(length)
  crypto.getRandomValues(values)
  return Array.from(values, v => alphabet[v % alphabet.length]).join('')
}

/** Validate, crop to a square, upload to the caller's own avatars folder, and save it on the profile. */
export async function uploadMyAvatar(userId: string, file: File): Promise<string> {
  if (!ACCEPTED_TYPES.includes(file.type)) throw new AvatarError('avatar_bad_type')
  if (file.size > AVATAR_MAX_BYTES) throw new AvatarError('avatar_too_large')
  const blob = await cropToSquareWebp(file)
  const path = `${userId}/${randomName()}.webp`
  const { error: uploadError } = await supabase.storage.from('avatars').upload(path, blob, {
    contentType: 'image/webp', upsert: false,
  })
  if (uploadError) throw uploadError
  const { data } = supabase.storage.from('avatars').getPublicUrl(path)
  const { error: rpcError } = await supabase.rpc('set_my_avatar', { p_url: data.publicUrl })
  if (rpcError) {
    await supabase.storage.from('avatars').remove([path]).catch(() => undefined)
    throw rpcError
  }
  return data.publicUrl
}

/** Clear the profile's avatar and best-effort remove the stored object. */
export async function removeMyAvatar(userId: string, currentUrl: string): Promise<void> {
  const { error } = await supabase.rpc('clear_my_avatar')
  if (error) throw error
  const match = currentUrl.match(/\/storage\/v1\/object\/public\/avatars\/([^?#]+)$/)
  if (match && match[1].startsWith(`${userId}/`)) {
    await supabase.storage.from('avatars').remove([match[1]]).catch(() => undefined)
  }
}
