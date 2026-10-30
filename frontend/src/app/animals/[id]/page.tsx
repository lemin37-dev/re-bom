import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import { findAnimal, searchAnimals } from '@/data/animals'
import { AnimalDetailScreen } from '@/screens/animal-detail/AnimalDetailScreen'

type Params = { params: Promise<{ id: string }> }

export function generateStaticParams() {
  return searchAnimals.map(a => ({ id: String(a.id) }))
}

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const animal = findAnimal(Number((await params).id))
  return { title: animal ? `${animal.kind} · ${animal.gender} · ${animal.age}살 | RE:봄` : 'RE:봄' }
}

export default async function AnimalPage({ params }: Params) {
  const animal = findAnimal(Number((await params).id))
  if (!animal) notFound()
  return <AnimalDetailScreen animal={animal} />
}
