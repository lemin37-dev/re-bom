export type MascotTheme = 'remeong' | 'bomnyang'

const poses = ['basic', 'greeting', 'curious', 'cheer', 'rest', 'sleep', 'walk', 'run'] as const

export const mascotProfiles = {
  remeong: {
    name: '리멍이',
    animal: '강아지',
    tagline: '천천히 다가가도 괜찮아, 내가 곁에 있을게.',
    description: '다정하고 차분하게 새로운 만남을 응원하는 친구예요. 처음의 낯섦도, 가족을 기다리는 시간도 조용히 함께해요.',
    personality: '다정함 · 차분함 · 따뜻한 응원',
    interpretation: '포근한 크림빛 털과 늘어진 귀는 편안함을, 작은 발걸음은 서두르지 않는 만남을 뜻해요.',
    poses,
  },
  bomnyang: {
    name: '봄냥이',
    animal: '고양이 · 코리안 숏헤어',
    tagline: '어떤 인연이 기다릴까? 우리 함께 찾아보자.',
    description: '호기심 많고 다정한 고양이예요. 사람과 동물을 좋아하고, 새로운 가족을 만나는 길을 가벼운 발걸음으로 함께해요.',
    personality: '호기심 · 다정함 · 사람과 동물을 좋아함',
    interpretation: '크림·살구·회색의 삼색 털은 서로 다른 친구들의 어울림을, 곁의 초록 새싹은 다시 찾아올 봄과 새로운 시작을 표현해요.',
    poses,
  },
} as const

export const mascotThemes = Object.keys(mascotProfiles) as MascotTheme[]

export const poseNames: Record<string, string> = {
  basic: '기본',
  greeting: '반가움',
  curious: '궁금함',
  cheer: '응원',
  rest: '편안함',
  sleep: '잠',
  walk: '걷기',
  run: '뛰기',
}
