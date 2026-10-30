import type { Animal, Shelter } from '@/types/animal'
import { searchAnimals } from './animals'

export const regions: Record<string, string[]> = {
  경기도: ['수원시', '성남시', '고양시'],
  서울특별시: ['마포구', '강동구', '노원구'],
  인천광역시: ['남동구', '연수구', '부평구'],
}

export const shelters: Shelter[] = searchAnimals.map((a, i) => {
  const region = Object.keys(regions)[i % 3]
  const district = regions[region][Math.floor(i / 3) % 3]
  return {
    name: a.center,
    region,
    district,
    address: `${region} ${district} 다시봄로 ${24 + i * 7} (가상 주소)`,
    phone: '000-0000-0000',
    hours: '10:00~17:00 · 점심 12:00~13:00',
    type: i % 2 ? '위탁' : '직영',
    distance: a.distance,
    count: searchAnimals.filter(p => p.center === a.center).length,
  }
})

export function shelterOf(animal: Animal) {
  return shelters.find(s => s.name === animal.center)!
}
