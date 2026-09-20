export { cn } from "cn"

export const plural = (count: number, noun: string, many = `${noun}s`) => `${count} ${count === 1 ? noun : many}`
