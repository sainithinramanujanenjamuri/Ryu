import React from "react";
import { TaskItem } from "../types";

interface TaskListViewProps {
  tasks: TaskItem[];
}

export const TaskListView: React.FC<TaskListViewProps> = ({ tasks }) => {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        background: "var(--ryu-black-800)",
        borderRadius: "6px",
        border: "1px solid var(--ryu-black-700)",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          padding: "10px 16px",
          borderBottom: "1px solid var(--ryu-black-700)",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <span style={{ fontSize: "12px", fontWeight: 600, color: "var(--ryu-text-100)" }}>
          Execution Plan Tasks
        </span>
        <span style={{ fontSize: "11px", color: "var(--ryu-text-400)", fontFamily: "var(--font-mono)" }}>
          {tasks.length} tasks
        </span>
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: "8px" }}>
        {tasks.length === 0 ? (
          <div style={{ padding: "24px", textAlign: "center", color: "var(--ryu-text-600)" }}>
            No tasks in active plan.
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
            {tasks.map((task) => (
              <div
                key={task.task_id}
                style={{
                  padding: "8px 12px",
                  background: "var(--ryu-black-900)",
                  border: "1px solid var(--ryu-black-700)",
                  borderRadius: "4px",
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <div>
                  <span style={{ fontSize: "12px", color: "var(--ryu-text-100)", fontWeight: 500 }}>
                    {task.title}
                  </span>
                  <div style={{ fontSize: "10px", color: "var(--ryu-text-600)", fontFamily: "var(--font-mono)" }}>
                    ID: {task.task_id}
                  </div>
                </div>
                <span
                  style={{
                    fontSize: "10px",
                    padding: "2px 6px",
                    borderRadius: "3px",
                    background: "var(--ryu-black-700)",
                    color: "var(--ryu-text-400)",
                    textTransform: "uppercase",
                  }}
                >
                  {task.status}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

