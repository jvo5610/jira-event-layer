import {
  ArrowRightLeft, Copy, FilePlus2, GitBranch, Link2,
  MessageSquare, Pencil, Plus, Search,
} from "lucide-react";
import { t, useLanguage } from "./i18n";
import { actionLabels, type Obj } from "./model";

const icons = {
  "jira.issue.get": Search,
  "jira.issue.create": FilePlus2,
  "jira.issue.clone": Copy,
  "jira.issue.edit": Pencil,
  "jira.issue.transition": ArrowRightLeft,
  "jira.comment.add": MessageSquare,
  "jira.issue.link": Link2,
  "bitbucket.pipeline": GitBranch,
};

export function ActionLibrary({ definitions, onAdd }: {
  definitions: Obj[];
  onAdd: (definition: Obj) => void;
}) {
  useLanguage();
  return (
    <div className="module-library">
      {([ ["jira", "Jira"], ["bitbucket", "Bitbucket"] ] as const).map(([provider, label]) => {
        const actions = Object.keys(actionLabels)
          .filter((type) => type.startsWith(provider + "."))
          .flatMap((type) => definitions.filter((def) => def.properties.type.const === type));
        if (!actions.length) return null;
        return (
          <section className="library-group" aria-label={label} key={provider}>
            <h4 className="library-group-heading">
              {label}<span>{actions.length}</span>
            </h4>
            <div className="library-actions">
              {actions.map((definition) => {
                const type = definition.properties.type.const as keyof typeof icons;
                const Icon = icons[type];
                return (
                  <button
                    key={type}
                    className="library-module"
                    aria-label={t("Agregar: {action}", { action: t(actionLabels[type]) })}
                    onClick={() => onAdd(definition)}
                  >
                    <span className={"app-icon " + (provider === "bitbucket" ? "bb" : "")} aria-hidden="true">
                      <Icon size={19} />
                    </span>
                    <span className="library-module-copy">
                      <strong>{t(actionLabels[type])}</strong>
                      <small>{type}</small>
                    </span>
                    <Plus className="library-module-add" size={17} aria-hidden="true" />
                  </button>
                );
              })}
            </div>
          </section>
        );
      })}
    </div>
  );
}
