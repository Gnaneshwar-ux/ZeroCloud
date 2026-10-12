import { mkdir, copyFile, readFile, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { feature } from 'topojson-client'
const target=resolve('android/app/src/main/assets/models')
await mkdir(target,{recursive:true})
for(const name of ['face_detection_yunet_2023mar.onnx','face_recognition_sface_2021dec.onnx']) {
  await copyFile(resolve('models/.deepface/weights',name),resolve(target,name))
}
for(const name of ['OpenCV-Zoo-LICENSE.txt','ONNX-Models-LICENSE.txt']) await copyFile(resolve('third-party',name),resolve(target,name))
const atlas=JSON.parse(await readFile(resolve('node_modules/world-atlas/land-110m.json'),'utf8'))
await writeFile(resolve('android/app/src/main/assets/land.json'),JSON.stringify(feature(atlas,atlas.objects.land).features[0].geometry))
console.log('Android offline face models prepared. No personal media is bundled.')
