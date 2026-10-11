declare module 'heic2any' {
  type ConversionOptions = {
    blob: Blob
    toType?: 'image/jpeg' | 'image/png' | 'image/gif'
    quality?: number
    multiple?: boolean
  }
  export default function heic2any(options: ConversionOptions): Promise<Blob | Blob[]>
}
