import { useState } from 'react'
import type { FormEvent } from 'react'
import { api } from './api'
import type { Project } from './api'

export interface ProjectRailProps {
  projects: Project[]
  selectedId: string | null
  onSelect: (id: string) => void
  onCreated: (project: Project) => void
  onDeleted: (id: string) => void
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : 'Something went wrong. Please try again.'
}

export function ProjectRail({
  projects,
  selectedId,
  onSelect,
  onCreated,
  onDeleted,
}: ProjectRailProps) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [isCreating, setIsCreating] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  const orderedProjects = [...projects].sort((left, right) =>
    right.created_at.localeCompare(left.created_at),
  )

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const trimmedName = name.trim()
    if (!trimmedName || isCreating) return

    setFormError(null)
    setIsCreating(true)
    try {
      const project = await api.createProject({
        name: trimmedName,
        ...(description.trim() ? { description: description.trim() } : {}),
      })
      onCreated(project)
      setName('')
      setDescription('')
    } catch (reason: unknown) {
      setFormError(errorMessage(reason))
    } finally {
      setIsCreating(false)
    }
  }

  async function handleDelete(project: Project) {
    if (deletingId) return
    const confirmed = window.confirm(
      `Delete “${project.name}”? This removes the project and its research data.`,
    )
    if (!confirmed) return

    setDeleteError(null)
    setDeletingId(project.id)
    try {
      await api.deleteProject(project.id)
      onDeleted(project.id)
    } catch (reason: unknown) {
      setDeleteError(errorMessage(reason))
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <aside className="project-rail" aria-label="Project navigation">
      <header className="project-rail-header">
        <h1>Portolan</h1>
        <p>Research workspace</p>
      </header>

      <nav className="project-list" aria-label="Projects">
        <div className="project-list-heading">
          <h2>Projects</h2>
          <span aria-label={`${projects.length} projects`}>{projects.length}</span>
        </div>
        {orderedProjects.length > 0 ? (
          <ul>
            {orderedProjects.map((project) => {
              const selected = project.id === selectedId
              const deleting = project.id === deletingId
              return (
                <li className="project-item" key={project.id}>
                  <button
                    className={`project-select${selected ? ' is-selected' : ''}`}
                    type="button"
                    aria-current={selected ? 'page' : undefined}
                    onClick={() => onSelect(project.id)}
                  >
                    <span className="project-name">{project.name}</span>
                    {project.description ? (
                      <span className="project-description">{project.description}</span>
                    ) : null}
                  </button>
                  <button
                    className="project-delete"
                    type="button"
                    aria-label={`Delete ${project.name}`}
                    disabled={deletingId !== null}
                    onClick={() => void handleDelete(project)}
                  >
                    {deleting ? '…' : '×'}
                  </button>
                </li>
              )
            })}
          </ul>
        ) : (
          <p className="project-list-empty">Create a project to begin.</p>
        )}
      </nav>

      {deleteError ? (
        <p className="rail-error" role="alert">
          {deleteError}
        </p>
      ) : null}

      <section className="new-project" aria-labelledby="new-project-heading">
        <h2 id="new-project-heading">New project</h2>
        <form onSubmit={(event) => void handleCreate(event)}>
          <label htmlFor="new-project-name">Name</label>
          <input
            id="new-project-name"
            name="name"
            type="text"
            value={name}
            required
            autoComplete="off"
            placeholder="e.g. Retrieval augmented generation"
            onChange={(event) => setName(event.target.value)}
          />

          <label htmlFor="new-project-description">Description (optional)</label>
          <textarea
            id="new-project-description"
            name="description"
            rows={2}
            value={description}
            placeholder="What are you researching?"
            onChange={(event) => setDescription(event.target.value)}
          />

          <button type="submit" disabled={isCreating || !name.trim()}>
            {isCreating ? 'Creating…' : 'Create project'}
          </button>
          {formError ? (
            <p className="form-error" role="alert">
              {formError}
            </p>
          ) : null}
        </form>
      </section>
    </aside>
  )
}

export default ProjectRail
