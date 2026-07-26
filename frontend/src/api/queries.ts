/** react-query 封装。组件不直接碰 fetch。 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { UseMutationResult, UseQueryResult } from '@tanstack/react-query'

import * as api from './client'
import type { DifferenceOut, DocumentOut, Envelope, ProjectOut, ReviewIn } from './types'

export const projectsKey = ['projects'] as const
export const projectKey = (projectId: string) => ['projects', projectId] as const
export const differencesKey = (projectId: string) =>
  ['projects', projectId, 'differences'] as const

export function useProjects(): UseQueryResult<Envelope<ProjectOut>> {
  return useQuery({ queryKey: projectsKey, queryFn: api.listProjects })
}

export function useProject(projectId: string): UseQueryResult<ProjectOut> {
  return useQuery({
    queryKey: projectKey(projectId),
    queryFn: () => api.getProject(projectId),
    enabled: projectId !== '',
  })
}

export function useDifferences(projectId: string): UseQueryResult<Envelope<DifferenceOut>> {
  return useQuery({
    queryKey: differencesKey(projectId),
    queryFn: () => api.listDifferences(projectId),
    enabled: projectId !== '',
  })
}

export function useCreateProject(): UseMutationResult<ProjectOut, Error, string> {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (name: string) => api.createProject(name),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: projectsKey })
    },
  })
}

export function useDeleteProject(): UseMutationResult<void, Error, string> {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (projectId: string) => api.deleteProject(projectId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: projectsKey })
    },
  })
}

export interface UploadVariables {
  role: string
  file: File
}

export function useUploadDocument(
  projectId: string,
): UseMutationResult<DocumentOut, Error, UploadVariables> {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ role, file }: UploadVariables) => api.uploadDocument(projectId, role, file),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: projectKey(projectId) })
    },
  })
}

export function useRunCompare(projectId: string): UseMutationResult<ProjectOut, Error, void> {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => api.runCompare(projectId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: projectKey(projectId) })
      void queryClient.invalidateQueries({ queryKey: differencesKey(projectId) })
    },
  })
}

export interface ReviewVariables {
  differenceKey: string
  body: ReviewIn
}

export function useSetReview(
  projectId: string,
): UseMutationResult<DifferenceOut, Error, ReviewVariables> {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ differenceKey, body }: ReviewVariables) =>
      api.setReview(projectId, differenceKey, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: differencesKey(projectId) })
      void queryClient.invalidateQueries({ queryKey: projectKey(projectId) })
    },
  })
}
